# aio-mini-chat
A port of mini\_chat to asyncio

This acutally has 1 fewer dependencies, and several fewer lines of code, than
the already miniscule mini\_chat.

# Features

- unlimited users
- unlimited channels
- per-channel topic
- per-channel nicks
- actions
- private messages
- nick completion on tab [appends a : if at start of line]
- per-IP rate limiting
- sanitised HTML
- linkified URLs

# QuickStart

Make sure you have Redis running, and listening on the default port.

```
$ virtualenv -p python3 achat
$ . achat/bin/activate
$ pip install -r requirements.txt
$ python chat.py
```

Now go to http://localhost:8080/#test

By changing the #fragment on the URL you change 'channels'.

Bonus Feature
-------------

If you put a copy of http://lab.ejci.net/favico.js/ v0.3.8 into the static/js dir, mini-chat will show you the number of un-seen messages in your favicon!
