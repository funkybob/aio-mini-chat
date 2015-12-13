
import asyncio
from functools import partial
import json
import os
import random
import string
import time

from aiohttp import web
from asyncio_redis import Connection, ZScoreBoundary

import bleach


BASE_DIR = os.path.dirname(__file__)

RATE_LIMIT_DURATION = 60
RATE_LIMIT = 10


def make_key(*args):
    return':'.join(args)


# For bleach
def linkify_external(attrs, new=False):
    attrs['target'] = '_blank'
    return attrs

strip_tags = partial(bleach.clean, tags=[], strip=True)


@asyncio.coroutine
def post_message(request, message, mode, queue=None, **data):
    if queue is None:
        queue = make_key(request.match_info['channel'], 'channel')

    nick = yield from get_nick(request)

    data.setdefault('message', message)
    data.setdefault('sender', nick)

    content = json.dumps(data)
    yield from request.conn.publish(queue, json.dumps([mode, content]))


# Nick handling
@asyncio.coroutine
def get_nicks(request):
    key = make_key(request.match_info['channel'], 'nick', '*')
    keys = yield from request.conn.keys_aslist(key)
    if keys:
        vals = yield from request.conn.mget_aslist(keys)
        return {
            k: v
            for k, v in zip(vals, keys)
        }
    return {}


@asyncio.coroutine
def get_nick(request):
    key = make_key(request.match_info['channel'], 'nick', request.tag)
    nick = yield from request.conn.get(key)
    if nick is None:
        nick = yield from set_nick(request, request.tag[:8])
    else:
        yield from request.conn.expire(key, 90)
    return nick


@asyncio.coroutine
def set_nick(request, name):
    name = strip_tags(name)
    nicks = yield from get_nicks(request)
    if name in nicks:
        raise ValueError('Nick in use!')
    key = make_key(request.match_info['channel'], 'nick', request.tag)
    yield from request.conn.set(key, name, expire=90)
    return name


# Topic handling
@asyncio.coroutine
def set_topic(request, topic):
    key = make_key(request.match_info['channel'], 'topic')
    yield from request.conn.set(key, topic)


@asyncio.coroutine
def get_topic(request):
    key = make_key(request.match_info['channel'], 'topic')
    topic = yield from request.conn.get(key)
    return topic


# Request handlers
@asyncio.coroutine
def index(request):
    return web.Response(
        body=open(os.path.join(BASE_DIR, 'index.html'), 'rb').read()
    )


class SseResponse(web.StreamResponse):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        self.headers[web.hdrs.CONTENT_TYPE] = 'text/event-stream; charset=utf-8'
        self.headers['Cache-Control'] = 'no-cache'

    def write_eof(self):
        request = self.request

        subscriber = yield from request.conn.start_subscribe()

        yield from subscriber.subscribe([
            make_key(request.match_info['channel'], 'channel'),
            make_key(request.tag, 'private'),
        ])

        while True:
            msg = yield from subscriber.next_published()
            mode, data = json.loads(msg.value)
            self.write('event: {}\n'.format(mode).encode('utf-8'))
            for line in data.splitlines():
                self.write('data: {}\n'.format(line).encode('utf-8'))
            self.write('\n'.encode('utf-8'))

        self.conn.close()


@asyncio.coroutine
def listen(request):
    if 'text/event-stream' not in request.headers['ACCEPT']:
        return web.http.HTTPNotAcceptable()

    nick = yield from get_nick(request)

    yield from post_message(request, '{} connected.'.format(nick), 'join',
                            sender='Notice')

    return SseResponse(request)


@asyncio.coroutine
def chatter(request):
    yield from request.post()

    mode = request.POST.get('mode', 'message')
    msg = request.POST.get('message', '')
    msg = bleach.linkify(strip_tags(msg), callbacks=[linkify_external])

    nick = yield from get_nick(request)

    if mode == 'nick' and msg:
        try:
            new_nick = yield from set_nick(request, msg)
        except ValueError:
            yield from post_message(request, 'Nick in use!', 'alert',
                                    sender='Notice')
        else:
            yield from post_message(
                request,
                '{} is now known as {}'.format(nick, new_nick),
                mode='nick',
                sender='Notice'
            )

    elif mode == 'names':
        nicks = yield from get_nicks(request)
        yield from post_message(request, list(nicks.keys()), 'names')

    elif mode == 'msg':
        target = request.POST['target']
        nicks = yield from get_nicks(request)
        _, _, target_tag = nicks[target].split(':')
        yield from post_message(request, msg, 'msg', target=target,
                                queue=make_key(target_tag, 'private'))
        yield from post_message(request, msg, 'msg', target=target,
                                queue=make_key(request.tag, 'private'))

    elif mode in ['message', 'action']:
        yield from post_message(request, msg, mode)

    elif mode == 'topic':
        if msg:
            yield from set_topic(request, msg)
        topic = yield from get_topic(request)
        yield from post_message(request, topic, 'topic')

    return web.Response(body=b'')


@asyncio.coroutine
def cookie_middleware(app, handler):
    @asyncio.coroutine
    def middleware(request):
        tag = request.cookies.get('chatterbox', None)
        request.tag = tag or ''.join(random.choice(string.ascii_letters)
                                     for x in range(16))

        request.conn = yield from Connection.create(host='localhost', port=6379)

        # Rate limit
        key = make_key(request.tag, 'rated')
        now = time.time()
        yield from request.conn.zadd(key, {str(int(now)): now})
        yield from request.conn.expireat(key, int(now) + RATE_LIMIT_DURATION)
        yield from request.conn.zremrangebyscore(
            key,
            ZScoreBoundary('-inf'),
            ZScoreBoundary(now - RATE_LIMIT_DURATION)
        )
        size = yield from request.conn.zcard(key)

        if size > RATE_LIMIT:
            response = web.Response(body=b'', status=429)
        else:
            # Call handler
            response = yield from handler(request)
        # Set cookie
        if tag is None:
            response.set_cookie('chatterbox', request.tag)
        if not isinstance(response, SseResponse):
            request.conn.close()
        return response
    return middleware

if __name__ == '__main__':
    app = web.Application(middlewares=[cookie_middleware])
    app.router.add_route('GET', '/', index)
    app.router.add_static('/static/', os.path.join(BASE_DIR, 'static'))
    app.router.add_route('GET', '/{channel}/', listen)
    app.router.add_route('POST', '/{channel}/', chatter)

    loop = asyncio.get_event_loop()
    f = loop.create_server(app.make_handler(), '0.0.0.0', 8080)
    srv = loop.run_until_complete(f)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
