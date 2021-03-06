import asyncio
import sys
import pytest
import random
import inspect
import asynctest

from asynctest import Mock, CoroutineMock, ANY

from aiocache import cached, cached_stampede, multi_cached, SimpleMemoryCache
from aiocache.base import _Conn
from aiocache.serializers import JsonSerializer


async def stub(*args, value=None, seconds=0, **kwargs):
    await asyncio.sleep(seconds)
    if value:
        return str(value)
    return str(random.randint(1, 50))


class TestCached:

    @pytest.fixture
    def decorator(self, mocker, mock_cache):
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):
            yield cached()

    @pytest.fixture
    def decorator_call(self, decorator):
        d = decorator(stub)
        decorator._conn = decorator.cache.get_connection()
        yield d

    @pytest.fixture(autouse=True)
    def spy_stub(self, mocker):
        module = sys.modules[globals()['__name__']]
        mocker.spy(module, 'stub')

    def test_init(self):
        c = cached(
            ttl=1, key="key", key_from_attr="key_attr", cache=SimpleMemoryCache,
            plugins=None, alias=None, noself=False, namespace="test")

        assert c.ttl == 1
        assert c.key == "key"
        assert c.key_from_attr == "key_attr"
        assert c.cache is None
        assert c._conn is None
        assert c._cache == SimpleMemoryCache
        assert c._serializer == JsonSerializer
        assert c._kwargs == {'namespace': 'test'}

    def test_fails_at_instantiation(self):
        with pytest.raises(TypeError):
            @cached(wrong_param=1)
            async def fn(n):
                return n

    def test_alias_takes_precedence(self, mock_cache):
        with asynctest.patch(
                "aiocache.decorators.caches.create",
                Mock(return_value=mock_cache)) as mock_create:
            c = cached(alias='default', cache=SimpleMemoryCache, namespace='test')
            c(stub)

            mock_create.assert_called_with('default')
            assert c.cache is mock_cache

    def test_get_cache_key_with_key(self, decorator):
        decorator.key = "key"
        decorator.key_from_attr = "ignore_me"
        assert decorator.get_cache_key(stub, (1, 2), {'a': 1, 'b': 2}) == 'key'

    def test_get_cache_key_with_key_attr(self, decorator):
        decorator.key_from_attr = "pick_me"
        assert decorator.get_cache_key(stub, (1, 2), {'pick_me': "key"}) == 'key'

    def test_get_cache_key_without_key_and_attr(self, decorator):
        assert decorator.get_cache_key(
            stub, (1, 2), {'a': 1, 'b': 2}) == "stub(1, 2)[('a', 1), ('b', 2)]"

    def test_get_cache_key_without_key_and_attr_noself(self, decorator):
        decorator.noself = True
        assert decorator.get_cache_key(
            stub, ('self', 1, 2), {'a': 1, 'b': 2}) == "stub(1, 2)[('a', 1), ('b', 2)]"

    @pytest.mark.asyncio
    async def test_calls_get_and_returns(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=1)

        await decorator_call()

        decorator.cache.get.assert_called_with('stub()[]', _conn=ANY)
        assert decorator.cache.set.call_count == 0
        assert stub.call_count == 0

    @pytest.mark.asyncio
    async def test_get_from_cache_returns(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=1)
        assert await decorator.get_from_cache("key") == 1

    @pytest.mark.asyncio
    async def test_get_from_cache_exception(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(side_effect=Exception)
        assert await decorator.get_from_cache("key") is None

    @pytest.mark.asyncio
    async def test_get_from_cache_none(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        assert await decorator.get_from_cache("key") is None

    @pytest.mark.asyncio
    async def test_get_from_cache_conn(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=1)
        decorator._conn._conn = Mock()
        await decorator.get_from_cache("key")
        decorator.cache.get.assert_called_with(pytest.KEY, _conn=decorator._conn._conn)

    @pytest.mark.asyncio
    async def test_calls_fn_set_when_get_none(self, mocker, decorator, decorator_call):
        mocker.spy(decorator, 'get_from_cache')
        mocker.spy(decorator, 'set_in_cache')
        decorator.cache.get = CoroutineMock(return_value=None)

        await decorator_call(value="value")

        assert decorator.get_from_cache.call_count == 1
        decorator.set_in_cache.assert_called_with("stub()[('value', 'value')]", "value")
        stub.assert_called_once_with(value="value")

    @pytest.mark.asyncio
    async def test_calls_fn_raises_exception(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        stub.side_effect = Exception()
        with pytest.raises(Exception):
            assert await decorator_call()

    @pytest.mark.asyncio
    async def test_set_calls_set(self, decorator, decorator_call):
        await decorator.set_in_cache("key", "value")
        decorator.cache.set.assert_called_with("key", "value", _conn=ANY, ttl=None)

    @pytest.mark.asyncio
    async def test_set_calls_set_ttl(self, decorator, decorator_call):
        decorator.ttl = 10
        await decorator.set_in_cache("key", "value")
        decorator.cache.set.assert_called_with("key", "value", _conn=ANY, ttl=decorator.ttl)

    @pytest.mark.asyncio
    async def test_set_catches_exception(self, decorator, decorator_call):
        decorator.cache.set = CoroutineMock(side_effect=Exception)
        assert await decorator.set_in_cache("key", "value") is None

    @pytest.mark.asyncio
    async def test_set_with_conn(self, decorator, decorator_call):
        decorator._conn._conn = Mock()
        await decorator.set_in_cache("key", "value")
        decorator.cache.set.assert_called_with(
            "key", "value", _conn=decorator._conn._conn, ttl=None)

    @pytest.mark.asyncio
    async def test_decorate(self, mock_cache):
        mock_cache.get = CoroutineMock(return_value=None)
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):
            @cached()
            async def fn(n):
                return n

            assert await fn(1) == 1
            assert await fn(2) == 2

    @pytest.mark.asyncio
    async def test_cached_keeps_signature(self, mock_cache):
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):
            @cached()
            async def what(self, a, b):
                return "1"

            assert what.__name__ == "what"
            assert str(inspect.signature(what)) == '(self, a, b)'
            assert inspect.getfullargspec(what.__wrapped__).args == ['self', 'a', 'b']

    @pytest.mark.asyncio
    async def test_reuses_connection(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        await decorator_call(value="value")

        assert decorator._conn._conn is not None
        decorator.cache.get.assert_called_with(
            "stub()[('value', 'value')]", _conn=decorator._conn._conn)
        decorator.cache.set.assert_called_with(
            "stub()[('value', 'value')]", 'value',  _conn=decorator._conn._conn, ttl=None)

    @pytest.mark.asyncio
    async def test_different_connection_per_call(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        conn1, conn2 = _Conn(decorator.cache), _Conn(decorator.cache)
        mocker.spy(conn1, 'get')
        mocker.spy(conn1, 'set')
        mocker.spy(conn2, 'get')
        mocker.spy(conn2, 'set')
        decorator.cache.get_connection = Mock(side_effect=[conn1, conn2])
        await decorator_call(value="value")
        await decorator_call(value="value")

        conn1.get.call_count = 1
        conn1.set.call_count = 1
        conn2.get.call_count = 1
        conn2.set.call_count = 1


class TestCachedStampede:
    @pytest.fixture
    def decorator(self, mocker, mock_cache):
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):
            yield cached_stampede()

    @pytest.fixture
    def decorator_call(self, decorator):
        yield decorator(stub)

    @pytest.fixture(autouse=True)
    def spy_stub(self, mocker):
        module = sys.modules[globals()['__name__']]
        mocker.spy(module, 'stub')

    def test_inheritance(self):
        assert isinstance(cached_stampede(), cached)

    def test_init(self):
        c = cached_stampede(
            lease=3, ttl=1, key="key", key_from_attr="key_attr", cache=SimpleMemoryCache,
            plugins=None, alias=None, noself=False, namespace="test")

        assert c.ttl == 1
        assert c.key == "key"
        assert c.key_from_attr == "key_attr"
        assert c.cache is None
        assert c._cache == SimpleMemoryCache
        assert c._serializer == JsonSerializer
        assert c.lease == 3
        assert c._kwargs == {'namespace': 'test'}

    @pytest.mark.asyncio
    async def test_calls_get_and_returns(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=1)

        await decorator_call()

        decorator.cache.get.assert_called_with('stub()[]')
        assert decorator.cache.set.call_count == 0
        assert stub.call_count == 0

    @pytest.mark.asyncio
    async def test_calls_fn_raises_exception(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        stub.side_effect = Exception()
        with pytest.raises(Exception):
            assert await decorator_call()

    @pytest.mark.asyncio
    async def test_calls_redlock(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)

        await decorator_call(value="value")

        assert decorator.cache.get.call_count == 2
        assert decorator.cache._redlock.call_count == 1
        decorator.cache.set.assert_called_with(
            "stub()[('value', 'value')]", "value", ttl=None)
        stub.assert_called_once_with(value="value")

    @pytest.mark.asyncio
    async def test_calls_locked_client(self, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(side_effect=[None, None, None, "value"])
        decorator.cache._add = CoroutineMock(side_effect=[True, ValueError])
        decorator.cache._redlock_release = CoroutineMock(side_effect=[1, 0])

        await asyncio.gather(decorator_call(value="value"), decorator_call(value="value"))

        assert decorator.cache.get.call_count == 4
        assert decorator.cache._redlock.call_count == 2
        decorator.cache.set.assert_called_with(
            "stub()[('value', 'value')]", "value", ttl=None)
        assert stub.call_count == 1

    @pytest.mark.asyncio
    async def test_doesnt_reuse_connection(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        await decorator_call(value="value")

        assert decorator._conn is None
        decorator.cache.get.assert_called_with(
            "stub()[('value', 'value')]")
        decorator.cache.set.assert_called_with(
            "stub()[('value', 'value')]", 'value', ttl=None)


async def stub_dict(*args, keys=None, **kwargs):
    values = {
        'a': random.randint(1, 50),
        'b': random.randint(1, 50),
        'c': random.randint(1, 50)
    }
    return {k: values.get(k) for k in keys}


class TestMultiCached:

    @pytest.fixture
    def decorator(self, mocker, mock_cache):
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):
            yield multi_cached(keys_from_attr="keys")

    @pytest.fixture
    def decorator_call(self, decorator):
        d = decorator(stub_dict)
        decorator._conn = decorator.cache.get_connection()
        yield d

    @pytest.fixture(autouse=True)
    def spy_stub_dict(self, mocker):
        module = sys.modules[globals()['__name__']]
        mocker.spy(module, 'stub_dict')

    def test_init(self):
        mc = multi_cached(
            keys_from_attr="keys", key_builder=None, ttl=1, cache=SimpleMemoryCache,
            plugins=None, alias=None, namespace="test")

        assert mc.ttl == 1
        assert mc.key_builder is None
        assert mc.keys_from_attr == "keys"
        assert mc.cache is None
        assert mc._cache == SimpleMemoryCache
        assert mc._serializer == JsonSerializer
        assert mc._kwargs == {'namespace': 'test'}

    def test_fails_at_instantiation(self):
        with pytest.raises(TypeError):
            @multi_cached(wrong_param=1)
            async def fn(n):
                return n

    def test_alias_takes_precedence(self, mock_cache):
        with asynctest.patch(
                "aiocache.decorators.caches.create",
                Mock(return_value=mock_cache)) as mock_create:
            mc = multi_cached(
                keys_from_attr="keys", alias='default', cache=SimpleMemoryCache, namespace='test')
            mc(stub_dict)

            mock_create.assert_called_with('default')
            assert mc.cache is mock_cache

    def test_get_cache_keys(self, decorator):
        assert decorator.get_cache_keys(stub_dict, (), {'keys': ['a', 'b']}) == ['a', 'b']

    def test_get_cache_keys_empty_list(self, decorator):
        assert decorator.get_cache_keys(stub_dict, (), {'keys': []}) == []

    def test_get_cache_keys_missing_kwarg(self, decorator):
        with pytest.raises(KeyError):
            assert decorator.get_cache_keys(stub_dict, (), {})

    def test_get_cache_keys_with_key_builder(self, decorator):
        decorator._key_builder = lambda x, y: y['market'] + '_' + x.upper()
        assert decorator.get_cache_keys(
            stub_dict, (), {'keys': ['a', 'b'], 'market': 'ES'}) == ['ES_A', 'ES_B']

    @pytest.mark.asyncio
    async def test_get_from_cache(self, decorator, decorator_call):
        decorator.cache.multi_get = CoroutineMock(return_value=[1, 2, 3])

        assert await decorator.get_from_cache('a', 'b', 'c') == [1, 2, 3]
        decorator.cache.multi_get.assert_called_with(('a', 'b', 'c'), _conn=ANY)

    @pytest.mark.asyncio
    async def test_get_from_cache_no_keys(self, decorator, decorator_call):
        assert await decorator.get_from_cache() == []
        assert decorator.cache.multi_get.call_count == 0

    @pytest.mark.asyncio
    async def test_get_from_cache_exception(self, decorator, decorator_call):
        decorator.cache.multi_get = CoroutineMock(side_effect=Exception)

        assert await decorator.get_from_cache('a', 'b', 'c') == [None, None, None]
        decorator.cache.multi_get.assert_called_with(('a', 'b', 'c'), _conn=ANY)

    @pytest.mark.asyncio
    async def test_get_from_cache_conn(self, decorator, decorator_call):
        decorator._conn._conn = Mock()
        decorator.cache.multi_get = CoroutineMock(return_value=[1, 2, 3])

        assert await decorator.get_from_cache('a', 'b', 'c') == [1, 2, 3]
        decorator.cache.multi_get.assert_called_with(('a', 'b', 'c'), _conn=decorator._conn._conn)

    @pytest.mark.asyncio
    async def test_calls_no_keys(self, decorator, decorator_call):
        await decorator_call(keys=[])
        assert decorator.cache.multi_get.call_count == 0
        assert stub_dict.call_count == 1

    @pytest.mark.asyncio
    async def test_returns_from_multi_set(self, mocker, decorator, decorator_call):
        mocker.spy(decorator, 'get_from_cache')
        mocker.spy(decorator, 'set_in_cache')
        decorator.cache.multi_get = CoroutineMock(return_value=[1, 2])

        assert await decorator_call(1, keys=['a', 'b']) == {'a': 1, 'b': 2}
        decorator.get_from_cache.assert_called_once_with('a', 'b')
        assert decorator.set_in_cache.call_count == 0
        assert stub_dict.call_count == 0

    @pytest.mark.asyncio
    async def test_calls_fn_multi_set_when_multi_get_none(self, mocker, decorator, decorator_call):
        mocker.spy(decorator, 'get_from_cache')
        mocker.spy(decorator, 'set_in_cache')
        decorator.cache.multi_get = CoroutineMock(return_value=[None, None])

        ret = await decorator_call(1, keys=['a', 'b'], value='value')

        decorator.get_from_cache.assert_called_once_with('a', 'b')
        decorator.set_in_cache.assert_called_with(ret)
        stub_dict.assert_called_once_with(1, keys=['a', 'b'], value="value")

    @pytest.mark.asyncio
    async def test_calls_fn_with_only_missing_keys(self, mocker, decorator, decorator_call):
        mocker.spy(decorator, 'set_in_cache')
        decorator.cache.multi_get = CoroutineMock(return_value=[1, None])

        assert await decorator_call(1, keys=['a', 'b'], value='value') == {'a': ANY, 'b': ANY}

        decorator.set_in_cache.assert_called_once_with({'a': ANY, 'b': ANY})
        stub_dict.assert_called_once_with(1, keys=['b'], value="value")

    @pytest.mark.asyncio
    async def test_calls_fn_raises_exception(self, mocker, decorator, decorator_call):
        decorator.cache.multi_get = CoroutineMock(return_value=[None])
        stub_dict.side_effect = Exception()
        with pytest.raises(Exception):
            assert await decorator_call(keys=[])

    @pytest.mark.asyncio
    async def test_set_in_cache(self, decorator, decorator_call):
        await decorator.set_in_cache({'a': 1, 'b': 2})

        call_args = decorator.cache.multi_set.call_args[0][0]
        assert ('a', 1) in call_args
        assert ('b', 2) in call_args
        call_args = decorator.cache.multi_set.call_args[1]['ttl'] is None

    @pytest.mark.asyncio
    async def test_set_in_cache_with_ttl(self, decorator, decorator_call):
        decorator.ttl = 10
        await decorator.set_in_cache({'a': 1, 'b': 2})

        assert decorator.cache.multi_set.call_args[1]['ttl'] == decorator.ttl

    @pytest.mark.asyncio
    async def test_set_in_cache_exception(self, decorator, decorator_call):
        decorator.cache.multi_set = CoroutineMock(side_effect=Exception)

        assert await decorator.set_in_cache({'a': 1, 'b': 2}) is None

    @pytest.mark.asyncio
    async def test_decorate(self, mock_cache):
        mock_cache.multi_get = CoroutineMock(return_value=[None])
        with asynctest.patch("aiocache.decorators._get_cache", return_value=mock_cache):

            @multi_cached(keys_from_attr="keys")
            async def fn(keys=None):
                return {'test': 1}

            assert await fn(keys=['test']) == {'test': 1}
            assert await fn(keys=['test']) == {'test': 1}

    @pytest.mark.asyncio
    async def test_cached_keeps_signature(self):
        @multi_cached(keys_from_attr="keys")
        async def what(self, keys=None, what=1):
            return "1"

        assert what.__name__ == "what"
        assert str(inspect.signature(what)) == '(self, keys=None, what=1)'
        assert inspect.getfullargspec(what.__wrapped__).args == ['self', 'keys', 'what']

    @pytest.mark.asyncio
    async def test_reuses_connection(self, mocker, decorator, decorator_call):
        decorator.cache.multi_get = CoroutineMock(return_value=[None])
        await decorator_call(keys=[pytest.KEY])

        assert decorator._conn._conn is not None
        decorator.cache.multi_get.assert_called_with(
            ('key',), _conn=decorator._conn._conn)
        decorator.cache.multi_set.assert_called_with(
            [('key', None)],  _conn=decorator._conn._conn, ttl=0)

    @pytest.mark.asyncio
    async def test_different_connection_per_call(self, mocker, decorator, decorator_call):
        decorator.cache.get = CoroutineMock(return_value=None)
        conn1, conn2 = _Conn(decorator.cache), _Conn(decorator.cache)
        mocker.spy(conn1, 'multi_get')
        mocker.spy(conn1, 'multi_set')
        mocker.spy(conn2, 'multi_get')
        mocker.spy(conn2, 'multi_set')
        decorator.cache.get_connection = Mock(side_effect=[conn1, conn2])
        await decorator_call(keys=[pytest.KEY])
        await decorator_call(keys=[pytest.KEY])

        conn1.multi_get.call_count = 1
        conn1.multi_set.call_count = 1
        conn2.multi_get.call_count = 1
        conn2.multi_set.call_count = 1
