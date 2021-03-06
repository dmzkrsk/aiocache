import asyncio
import inspect
import functools

from aiocache.log import logger
from aiocache import SimpleMemoryCache, caches
from aiocache.serializers import JsonSerializer


class cached:
    """
    Caches the functions return value into a key generated with module_name, function_name and args.

    In some cases you will need to send more args to configure the cache object.
    An example would be endpoint and port for the RedisCache. You can send those args as
    kwargs and they will be propagated accordingly.

    Each call will use the same connection through all the cache calls. If you expect high
    concurrency for the function you are decorating, it will be safer if you set high pool sizes
    (in case of using memcached or redis).

    :param ttl: int seconds to store the function call. Default is None which means no expiration.
    :param key: str value to set as key for the function return. Takes precedence over
        key_from_attr param. If key and key_from_attr are not passed, it will use module_name
        + function_name + args + kwargs
    :param key_from_attr: str arg or kwarg name from the function to use as a key.
    :param cache: cache class to use when calling the ``set``/``get`` operations.
        Default is ``aiocache.SimpleMemoryCache``.
    :param serializer: serializer instance to use when calling the ``dumps``/``loads``.
        Default is JsonSerializer.
    :param plugins: list plugins to use when calling the cmd hooks
        Default is pulled from the cache class being used.
    :param alias: str specifying the alias to load the config from. If alias is passed, other config
        parameters are ignored. New cache is created every time.
    :param noself: bool if you are decorating a class function, by default self is also used to
        generate the key. This will result in same function calls done by different class instances
        to use different cache keys. Use noself=True if you want to ignore it.
    """

    def __init__(
            self, ttl=None, key=None, key_from_attr=None, cache=SimpleMemoryCache,
            serializer=JsonSerializer, plugins=None, alias=None, noself=False, **kwargs):
        self.ttl = ttl
        self.key = key
        self.key_from_attr = key_from_attr
        self.noself = noself
        self.alias = alias
        self.cache = None
        self._conn = None

        self._cache = cache
        self._serializer = serializer
        self._plugins = plugins
        self._kwargs = kwargs

    @property
    def conn(self):
        return self._conn

    def __call__(self, f):

        if self.alias:
            self.cache = caches.create(self.alias)
        else:
            self.cache = _get_cache(
                cache=self._cache, serializer=self._serializer,
                plugins=self._plugins, **self._kwargs)

        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            return await self.decorator(f, *args, **kwargs)
        return wrapper

    async def decorator(self, f, *args, **kwargs):
        self._conn = self.cache.get_connection()

        async with self._conn:
            key = self.get_cache_key(f, args, kwargs)

            value = await self.get_from_cache(key)
            if value is not None:
                return value

            result = await f(*args, **kwargs)

            await self.set_in_cache(key, result)

        return result

    def get_cache_key(self, f, args, kwargs):
        if self.key:
            return self.key

        args_dict = _get_args_dict(f, args, kwargs)
        cache_key = args_dict.get(
            self.key_from_attr, self._key_from_args(f, args, kwargs))
        return cache_key

    def _key_from_args(self, func, args, kwargs):
        ordered_kwargs = sorted(kwargs.items())
        return (func.__module__ or '') + func.__name__ + str(
            args[1:] if self.noself else args) + str(ordered_kwargs)

    async def get_from_cache(self, key):
        try:
            value = await self.conn.get(key)
            if value is not None:
                asyncio.ensure_future(self.cache.close())
            return value
        except Exception:
            logger.exception("Couldn't retrieve %s, unexpected error", key)

    async def set_in_cache(self, key, value):
        try:
            await self.conn.set(key, value, ttl=self.ttl)
        except Exception:
            logger.exception("Couldn't set %s in key %s, unexpected error", value, key)


class cached_stampede(cached):
    """
    Caches the functions return value into a key generated with module_name, function_name and args
    while avoids for cache stampede effects.

    In some cases you will need to send more args to configure the cache object.
    An example would be endpoint and port for the RedisCache. You can send those args as
    kwargs and they will be propagated accordingly.

    This decorator doesn't reuse connections because it would lock the connection while its
    locked waiting for the first call to finish calculating and this is counterproductive.

    :param lease: int seconds to lock function call to avoid cache stampede effects.
        If 0 or None, no locking happens (default is 2). redis and memory backends support
        float ttls
    :param ttl: int seconds to store the function call. Default is None which means no expiration.
    :param key: str value to set as key for the function return. Takes precedence over
        key_from_attr param. If key and key_from_attr are not passed, it will use module_name
        + function_name + args + kwargs
    :param key_from_attr: str arg or kwarg name from the function to use as a key.
    :param cache: cache class to use when calling the ``set``/``get`` operations.
        Default is ``aiocache.SimpleMemoryCache``.
    :param serializer: serializer instance to use when calling the ``dumps``/``loads``.
        Default is JsonSerializer.
    :param plugins: list plugins to use when calling the cmd hooks
        Default is pulled from the cache class being used.
    :param alias: str specifying the alias to load the config from. If alias is passed, other config
        parameters are ignored. New cache is created every time.
    :param noself: bool if you are decorating a class function, by default self is also used to
        generate the key. This will result in same function calls done by different class instances
        to use different cache keys. Use noself=True if you want to ignore it.
    """
    def __init__(
            self, lease=2, **kwargs):
        super().__init__(**kwargs)
        self.lease = lease

    @property
    def conn(self):
        return self.cache

    async def decorator(self, f, *args, **kwargs):
        key = self.get_cache_key(f, args, kwargs)

        value = await self.get_from_cache(key)
        if value is not None:
            return value

        async with self.conn._redlock(key, self.lease):
            value = await self.get_from_cache(key)
            if value is not None:
                return value

            result = await f(*args, **kwargs)

            await self.set_in_cache(key, result)

        return result


def _get_cache(
        cache=SimpleMemoryCache, serializer=None, plugins=None, **cache_kwargs):
    return cache(serializer=serializer, plugins=plugins, **cache_kwargs)


def _get_args_dict(func, args, kwargs):
    defaults = {
        arg_name: arg.default for arg_name, arg in inspect.signature(func).parameters.items()
        if arg.default is not inspect._empty
    }
    args_names = func.__code__.co_varnames[:func.__code__.co_argcount]
    return {**defaults, **dict(zip(args_names, args)), **kwargs}


class multi_cached:
    """
    Only supports functions that return dict-like structures. This decorator caches each key/value
    of the dict-like object returned by the function.

    If key_builder is passed, before storing the key, it will be transformed according to the output
    of the function.

    If the attribute specified to be the key is an empty list, the cache will be ignored and
    the function will be called as expected.

    Each call will use the same connection through all the cache calls. If you expect high
    concurrency for the function you are decorating, it will be safer if you set high pool sizes
    (in case of using memcached or redis).

    :param keys_from_attr: arg or kwarg name from the function containing an iterable to use
        as keys to index in the cache.
    :param key_builder: Callable that allows to change the format of the keys before storing.
        Receives a dict with all the args of the function.
    :param ttl: int seconds to store the keys. Default is 0 which means no expiration.
    :param cache: cache class to use when calling the ``multi_set``/``multi_get`` operations.
        Default is ``aiocache.SimpleMemoryCache``.
    :param serializer: serializer instance to use when calling the ``dumps``/``loads``.
        Default is JsonSerializer.
    :param plugins: plugins to use when calling the cmd hooks
        Default is pulled from the cache class being used.
    :param alias: str specifying the alias to load the config from. If alias is passed, other config
        parameters are ignored. New cache is created every time.
    """

    def __init__(
            self, keys_from_attr, key_builder=None, ttl=0, cache=SimpleMemoryCache,
            serializer=JsonSerializer, plugins=None, alias=None, **kwargs):
        self.keys_from_attr = keys_from_attr
        self.key_builder = key_builder
        self.ttl = ttl
        self.alias = alias
        self.cache = None
        self._conn = None

        self._cache = cache
        self._serializer = serializer
        self._plugins = plugins
        self._kwargs = kwargs
        self._key_builder = key_builder or (lambda x, args_dict: x)

    def __call__(self, f):
        if self.alias:
            self.cache = caches.create(self.alias)
        else:
            self.cache = _get_cache(
                cache=self._cache, serializer=self._serializer,
                plugins=self._plugins, **self._kwargs)

        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            return await self.decorator(f, *args, **kwargs)
        return wrapper

    async def decorator(self, f, *args, **kwargs):
        self._conn = self.cache.get_connection()
        async with self._conn:
            missing_keys = []
            partial = {}
            keys = self.get_cache_keys(f, args, kwargs)

            values = await self.get_from_cache(*keys)
            for key, value in zip(keys, values):
                if value is None:
                    missing_keys.append(key)
                else:
                    partial[key] = value
            kwargs[self.keys_from_attr] = missing_keys
            if values and None not in values:
                return partial

            result = await f(*args, **kwargs)
            result.update(partial)

            await self.set_in_cache(result)

        return result

    def get_cache_keys(self, f, args, kwargs):
        args_dict = _get_args_dict(f, args, kwargs)
        keys = args_dict[self.keys_from_attr]
        return [self._key_builder(key, args_dict) for key in keys]

    async def get_from_cache(self, *keys):
        if not keys:
            return []
        try:
            values = await self._conn.multi_get(keys)
            if None not in values:
                asyncio.ensure_future(self.cache.close())
            return values
        except Exception:
            logger.exception("Couldn't retrieve %s, unexpected error", keys)
            return [None] * len(keys)

    async def set_in_cache(self, result):
        try:
            await self._conn.multi_set([(k, v) for k, v in result.items()], ttl=self.ttl)
        except Exception:
            logger.exception("Couldn't set %s, unexpected error", result)
