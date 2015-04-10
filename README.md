# aio-mini-chat
A port of mini\_chat to asyncio

This acutally has 1 fewer dependencies, and several fewer lines of code, than
the already miniscule mini\_chat.

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
