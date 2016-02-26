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


async def post_message(request, message, mode, queue=None, **data):
    if queue is None:
        queue = make_key(request.match_info['channel'], 'channel')

    nick = await get_nick(request)

    data.setdefault('message', message)
    data.setdefault('sender', nick)

    content = json.dumps(data)
    await request['conn'].publish(queue, json.dumps([mode, content]))


# Nick handling
async def get_nicks(request):
    key = make_key(request.match_info['channel'], 'nick', '*')
    keys = await request['conn'].keys_aslist(key)
    if keys:
        vals = await request['conn'].mget_aslist(keys)
        return {
            k: v
            for k, v in zip(vals, keys)
        }
    return {}


async def get_nick(request):
    key = make_key(request.match_info['channel'], 'nick', request.tag)
    nick = await request['conn'].get(key)
    if nick is None:
        nick = await set_nick(request, request.tag[:8])
    else:
        await request['conn'].expire(key, 90)
    return nick


async def set_nick(request, name):
    name = strip_tags(name)
    nicks = await get_nicks(request)
    if name in nicks:
        raise ValueError('Nick in use!')
    key = make_key(request.match_info['channel'], 'nick', request.tag)
    await request['conn'].set(key, name, expire=90)
    return name


# Topic handling
async def set_topic(request, topic):
    key = make_key(request.match_info['channel'], 'topic')
    await request['conn'].set(key, topic)


async def get_topic(request):
    key = make_key(request.match_info['channel'], 'topic')
    return await request['conn'].get(key)


# Request handlers
async def index(request):
    return web.Response(
        body=open(os.path.join(BASE_DIR, 'index.html'), 'rb').read()
    )


async def listen(request):
    if 'text/event-stream' not in request.headers['ACCEPT']:
        return web.http.HTTPNotAcceptable()

    nick = await get_nick(request)

    await post_message(request, '{} connected.'.format(nick), 'join', sender='Notice')

    resp = web.StreamResponse()
    resp.headers[web.hdrs.CONTENT_TYPE] = 'text/event-stream; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    await resp.prepare(request)

    subscriber = await request['conn'].start_subscribe()

    await subscriber.subscribe([
        make_key(request.match_info['channel'], 'channel'),
        make_key(request.tag, 'private'),
    ])

    while True:
        msg = await subscriber.next_published()
        mode, data = json.loads(msg.value)
        resp.write('event: {}\n'.format(mode).encode('utf-8'))
        for line in data.splitlines():
            resp.write('data: {}\n'.format(line).encode('utf-8'))
        resp.write('\n'.encode('utf-8'))

    return resp


async def chatter(request):
    await request.post()

    mode = request.POST.get('mode', 'message')
    msg = request.POST.get('message', '')
    msg = bleach.linkify(strip_tags(msg), callbacks=[linkify_external])

    nick = await get_nick(request)

    if mode == 'nick' and msg:
        try:
            new_nick = await set_nick(request, msg)
        except ValueError:
            await post_message(request, 'Nick in use!', 'alert',
                               sender='Notice')
        else:
            await post_message(
                request,
                '{} is now known as {}'.format(nick, new_nick),
                mode='nick',
                sender='Notice'
            )

    elif mode == 'names':
        nicks = await get_nicks(request)
        await post_message(request, list(nicks.keys()), 'names')

    elif mode == 'msg':
        target = request.POST['target']
        nicks = await get_nicks(request)
        _, _, target_tag = nicks[target].split(':')
        await post_message(request, msg, 'msg', target=target,
                                queue=make_key(target_tag, 'private'))
        await post_message(request, msg, 'msg', target=target,
                                queue=make_key(request.tag, 'private'))

    elif mode in ['message', 'action']:
        await post_message(request, msg, mode)

    elif mode == 'topic':
        if msg:
            await set_topic(request, msg)
        topic = await get_topic(request)
        await post_message(request, topic, 'topic')

    return web.Response(body=b'')


async def cookie_middleware(app, handler):
    async def middleware(request):
        tag = request.cookies.get('chatterbox', None)
        request.tag = tag or ''.join(random.choice(string.ascii_letters)
                                     for x in range(16))

        request['conn'] = await Connection.create(host='localhost', port=6379)

        # Rate limit
        key = make_key(request.tag, 'rated')
        now = time.time()
        await request['conn'].zadd(key, {str(int(now)): now})
        await request['conn'].expireat(key, int(now) + RATE_LIMIT_DURATION)
        await request['conn'].zremrangebyscore(
            key,
            ZScoreBoundary('-inf'),
            ZScoreBoundary(now - RATE_LIMIT_DURATION)
        )
        size = await request['conn'].zcard(key)

        if size > RATE_LIMIT:
            response = web.Response(body=b'', status=429)
        else:
            # Call handler
            response = await handler(request)
        # Set cookie
        if tag is None:
            response.set_cookie('chatterbox', request.tag)
        return response
    return middleware

if __name__ == '__main__':
    app = web.Application(middlewares=[cookie_middleware])
    app.router.add_route('GET', '/', index)
    app.router.add_static('/static/', os.path.join(BASE_DIR, 'static'))
    app.router.add_route('GET', '/{channel}/', listen)
    app.router.add_route('POST', '/{channel}/', chatter)

    loop = asyncio.get_event_loop()
    web.run_app(app, host='0.0.0.0', port=8080)
