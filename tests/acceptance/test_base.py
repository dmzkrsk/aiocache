import pytest
import asyncio

from aiocache import serializers, RedisCache, SimpleMemoryCache, MemcachedCache
from aiocache.base import _Conn


class TestCache:
    """
    This class ensures that all caches behave the same way and have the minimum functionality.
    To add a new cache just create the fixture for the new cache and add id as a param for the
    cache fixture
    """
    @pytest.mark.asyncio
    async def test_setup(self, cache):
        assert cache.namespace == "test"
        assert isinstance(cache.serializer, serializers.StringSerializer)

    @pytest.mark.asyncio
    async def test_get_missing(self, cache):
        assert await cache.get(pytest.KEY) is None
        assert await cache.get(pytest.KEY, default=1) == 1

    @pytest.mark.asyncio
    async def test_get_existing(self, cache):
        await cache.set(pytest.KEY, "value")
        assert await cache.get(pytest.KEY) == "value"

    @pytest.mark.asyncio
    async def test_multi_get(self, cache):
        await cache.set(pytest.KEY, "value")
        assert await cache.multi_get([pytest.KEY, pytest.KEY_1]) == ["value", None]

    @pytest.mark.asyncio
    async def test_delete_missing(self, cache):
        assert await cache.delete(pytest.KEY) == 0

    @pytest.mark.asyncio
    async def test_delete_existing(self, cache):
        await cache.set(pytest.KEY, b"value")
        assert await cache.delete(pytest.KEY) == 1

        assert await cache.get(pytest.KEY) is None

    @pytest.mark.asyncio
    async def test_set(self, cache):
        assert await cache.set(pytest.KEY, b"value") is True

    @pytest.mark.asyncio
    async def test_multi_set(self, cache):
        pairs = [(pytest.KEY, "value"), [pytest.KEY_1, "random_value"]]
        assert await cache.multi_set(pairs) is True
        assert await cache.multi_get([pytest.KEY, pytest.KEY_1]) == ["value", "random_value"]

    @pytest.mark.asyncio
    async def test_multi_set_with_ttl(self, cache):
        pairs = [(pytest.KEY, "value"), [pytest.KEY_1, "random_value"]]
        assert await cache.multi_set(pairs, ttl=1) is True
        await asyncio.sleep(1.1)

        assert await cache.multi_get([pytest.KEY, pytest.KEY_1]) == [None, None]

    @pytest.mark.asyncio
    async def test_set_with_ttl(self, cache):
        await cache.set(pytest.KEY, b"value", ttl=1)
        await asyncio.sleep(1.1)

        assert await cache.get(pytest.KEY) is None

    @pytest.mark.asyncio
    async def test_add_missing(self, cache):
        assert await cache.add(pytest.KEY, b"value", ttl=1) is True

    @pytest.mark.asyncio
    async def test_add_existing(self, cache):
        await cache.set(pytest.KEY, b"value") is True
        with pytest.raises(ValueError):
            await cache.add(pytest.KEY, b"value")

    @pytest.mark.asyncio
    async def test_exists_missing(self, cache):
        assert await cache.exists(pytest.KEY) is False

    @pytest.mark.asyncio
    async def test_exists_existing(self, cache):
        await cache.set(pytest.KEY, b"value")
        assert await cache.exists(pytest.KEY) is True

    @pytest.mark.asyncio
    async def test_increment_missing(self, cache):
        assert await cache.increment(pytest.KEY, delta=2) == 2
        assert await cache.increment(pytest.KEY_1, delta=-2) == -2

    @pytest.mark.asyncio
    async def test_increment_existing(self, cache):
        await cache.set(pytest.KEY, "2")
        assert await cache.increment(pytest.KEY, delta=2) == 4
        assert await cache.increment(pytest.KEY, delta=1) == 5
        assert await cache.increment(pytest.KEY, delta=-3) == 2

    @pytest.mark.asyncio
    async def test_increment_typeerror(self, cache):
        await cache.set(pytest.KEY, b"value")
        with pytest.raises(TypeError):
            assert await cache.increment(pytest.KEY)

    @pytest.mark.asyncio
    async def test_expire_existing(self, cache):
        await cache.set(pytest.KEY, b"value")
        assert await cache.expire(pytest.KEY, 1) is True
        await asyncio.sleep(1.1)
        assert await cache.exists(pytest.KEY) is False

    @pytest.mark.asyncio
    async def test_expire_with_0(self, cache):
        await cache.set(pytest.KEY, b"value", 1)
        assert await cache.expire(pytest.KEY, 0) is True
        await asyncio.sleep(1.1)
        assert await cache.exists(pytest.KEY) is True

    @pytest.mark.asyncio
    async def test_expire_missing(self, cache):
        assert await cache.expire(pytest.KEY, 1) is False

    @pytest.mark.asyncio
    async def test_clear(self, cache):
        await cache.set(pytest.KEY, "value")
        await cache.clear()

        assert await cache.exists(pytest.KEY) is False

    @pytest.mark.asyncio
    async def test_close_pool_only_clears_resources(self, cache):
        await cache.set(pytest.KEY, "value")
        await cache.close()
        assert await cache.set(pytest.KEY, "value") is True
        assert await cache.get(pytest.KEY) == "value"

    @pytest.mark.asyncio
    async def test_single_connection(self, cache):
        async with cache.get_connection() as conn:
            assert isinstance(conn, _Conn)
            assert await conn.set(pytest.KEY, "value") is True
            assert await conn.get(pytest.KEY) == "value"


class TestMemoryCache:

    @pytest.mark.asyncio
    async def test_accept_explicit_args(self):
        with pytest.raises(TypeError):
            SimpleMemoryCache(random_attr="wtf")

    @pytest.mark.asyncio
    async def test_set_float_ttl(self, memory_cache):
        await memory_cache.set(pytest.KEY, "value", ttl=0.1)
        await asyncio.sleep(0.15)

        assert await memory_cache.get(pytest.KEY) is None

    @pytest.mark.asyncio
    async def test_multi_set_float_ttl(self, memory_cache):
        pairs = [(pytest.KEY, "value"), [pytest.KEY_1, "random_value"]]
        assert await memory_cache.multi_set(pairs, ttl=0.1) is True
        await asyncio.sleep(0.15)

        assert await memory_cache.multi_get([pytest.KEY, pytest.KEY_1]) == [None, None]

    @pytest.mark.asyncio
    async def test_raw(self, memory_cache):
        await memory_cache.raw("setdefault", "key", "value")
        assert await memory_cache.raw("get", "key") == "value"
        assert list(await memory_cache.raw("keys")) == ["key"]

    @pytest.mark.asyncio
    async def test_clear_with_namespace_memory(self, memory_cache):
        await memory_cache.set(pytest.KEY, "value", namespace="test")
        await memory_cache.clear(namespace="test")

        assert await memory_cache.exists(pytest.KEY, namespace="test") is False

    @pytest.mark.asyncio
    async def test_locking_dogpile(self, mocker, cache):
        mocker.spy(cache, 'get')
        mocker.spy(cache, 'set')
        mocker.spy(cache, '_add')
        mocker.spy(cache, '_redlock_release')

        async def dummy():
            res = await cache.get(pytest.KEY)
            if res is not None:
                return res

            async with cache._redlock(pytest.KEY, lease=5):
                res = await cache.get(pytest.KEY)
                if res is not None:
                    return res
                await asyncio.sleep(0.1)
                await cache.set(pytest.KEY, "value")

        await asyncio.gather(dummy(), dummy(), dummy(), dummy())
        assert cache._add.call_count == 4
        assert cache._redlock_release.call_count == 4
        assert cache.get.call_count == 8
        assert cache.set.call_count == 1

    @pytest.mark.asyncio
    async def test_locking_dogpile_lease_expiration(self, mocker, cache):
        mocker.spy(cache, 'get')
        mocker.spy(cache, 'set')

        async def dummy():
            res = await cache.get(pytest.KEY)
            if res is not None:
                return res

            async with cache._redlock(pytest.KEY, lease=1):
                res = await cache.get(pytest.KEY)
                if res is not None:
                    return res
                await asyncio.sleep(1.1)
                await cache.set(pytest.KEY, "value")

        await asyncio.gather(dummy(), dummy(), dummy(), dummy())
        assert cache.get.call_count == 8
        assert cache.set.call_count == 4


class TestMemcachedCache:

    @pytest.mark.asyncio
    async def test_accept_explicit_args(self):
        with pytest.raises(TypeError):
            MemcachedCache(random_attr="wtf")

    @pytest.mark.asyncio
    async def test_set_float_ttl_fails(self, memcached_cache):
        with pytest.raises(TypeError):
            await memcached_cache.set(pytest.KEY, "value", ttl=0.1)

    @pytest.mark.asyncio
    async def test_multi_set_float_ttl(self, memcached_cache):
        with pytest.raises(TypeError):
            pairs = [(pytest.KEY, b"value"), [pytest.KEY_1, b"random_value"]]
            assert await memcached_cache.multi_set(pairs, ttl=0.1) is True

    @pytest.mark.asyncio
    async def test_raw(self, memcached_cache):
        await memcached_cache.raw("set", b"key", b"value")
        assert await memcached_cache.raw("get", b"key") == "value"
        assert await memcached_cache.raw("prepend", b"key", b"super") is True
        assert await memcached_cache.raw("get", b"key") == "supervalue"

    @pytest.mark.asyncio
    async def test_clear_with_namespace_memcached(self, memcached_cache):
        await memcached_cache.set(pytest.KEY, b"value", namespace="test")

        with pytest.raises(ValueError):
            await memcached_cache.clear(namespace="test")

        assert await memcached_cache.exists(pytest.KEY, namespace="test") is True

    @pytest.mark.asyncio
    async def test_close(self, memcached_cache):
        await memcached_cache.set(pytest.KEY, "value")
        await memcached_cache._close()
        assert memcached_cache.client._pool._pool.qsize() == 0


class TestRedisCache:

    @pytest.mark.asyncio
    async def test_accept_explicit_args(self):
        with pytest.raises(TypeError):
            RedisCache(random_attr="wtf")

    @pytest.mark.asyncio
    async def test_float_ttl(self, redis_cache):
        await redis_cache.set(pytest.KEY, "value", ttl=0.1)
        await asyncio.sleep(0.15)

        assert await redis_cache.get(pytest.KEY) is None

    @pytest.mark.asyncio
    async def test_multi_set_float_ttl(self, redis_cache):
        pairs = [(pytest.KEY, "value"), [pytest.KEY_1, "random_value"]]
        assert await redis_cache.multi_set(pairs, ttl=0.1) is True
        await asyncio.sleep(0.15)

        assert await redis_cache.multi_get([pytest.KEY, pytest.KEY_1]) == [None, None]

    @pytest.mark.asyncio
    async def test_raw(self, redis_cache):
        await redis_cache.raw("set", "key", "value")
        assert await redis_cache.raw("get", "key") == "value"
        assert await redis_cache.raw("keys", "k*") == ["key"]

    @pytest.mark.asyncio
    async def test_clear_with_namespace_redis(self, redis_cache):
        await redis_cache.set(pytest.KEY, "value", namespace="test")
        await redis_cache.clear(namespace="test")

        assert await redis_cache.exists(pytest.KEY, namespace="test") is False

    @pytest.mark.asyncio
    async def test_close(self, redis_cache):
        await redis_cache.set(pytest.KEY, "value")
        await redis_cache._close()
        assert redis_cache._pool.size == 0
