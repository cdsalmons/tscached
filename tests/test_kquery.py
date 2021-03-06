import datetime
import time
from types import GeneratorType

from freezegun import freeze_time
from mock import patch
import pytest

from testing.mock_redis import MockRedis
from tscached.kquery import KQuery
from tscached.utils import BackendQueryFailure


def test__init__etc():
    """ Test __init__, key_basis, add_mts. """
    kq = KQuery(MockRedis())
    kq.query = {'wubbalubba': 'dubdub'}
    assert kq.related_mts == set()
    kq.add_mts('hello')
    kq.add_mts('goodbye')
    kq.add_mts('hello')

    testset = set()
    testset.add('hello')
    testset.add('goodbye')
    assert kq.related_mts == testset
    assert kq.key_basis() == {'wubbalubba': 'dubdub'}


def test_from_request():
    redis_cli = MockRedis()
    example_request = {
                       'metrics': [{'hello': 'some query'}, {'goodbye': 'another_query'}],
                       'start_relative': {'value': '1', 'unit': 'hours'}
                      }
    ret_vals = KQuery.from_request(example_request, redis_cli)
    assert isinstance(ret_vals, GeneratorType)

    ctr = 0
    for kq in ret_vals:
        assert isinstance(kq, KQuery)
        assert kq.query == example_request['metrics'][ctr]
        ctr += 1
    assert redis_cli.set_call_count == 0 and redis_cli.get_call_count == 0


def test_from_request_replace_align_sampling():
    redis_cli = MockRedis()
    aggregator = {'name': 'sum', 'align_sampling': True, 'sampling': {'value': '1', 'unit': 'minutes'}}
    example_request = {
                       'metrics': [{'hello': 'some query', 'aggregators': [aggregator]}],
                       'start_relative': {'value': '1', 'unit': 'hours'}
                      }
    agg_out = {'name': 'sum', 'align_start_time': True, 'sampling': {'value': '1', 'unit': 'minutes'}}
    request_out = {
                   'metrics': [{'hello': 'some query', 'aggregators': [agg_out]}],
                   'start_relative': {'value': '1', 'unit': 'hours'}
                  }

    ret_vals = KQuery.from_request(example_request, redis_cli)
    assert isinstance(ret_vals, GeneratorType)
    ret_vals = list(ret_vals)
    assert len(ret_vals) == 1
    assert isinstance(ret_vals[0], KQuery)
    assert ret_vals[0].query == request_out['metrics'][0]
    assert redis_cli.set_call_count == 0 and redis_cli.get_call_count == 0


def test_from_cache():
    redis_cli = MockRedis()
    keys = ['tscached:kquery:deadbeef', 'tscached:kquery:deadcafe']
    ret = KQuery.from_cache(keys, redis_cli)
    assert isinstance(ret, GeneratorType)
    values = list(ret)
    assert redis_cli.derived_pipeline.pipe_get_call_count == 2
    assert redis_cli.derived_pipeline.execute_count == 1
    ctr = 0
    for kq in values:
        assert isinstance(kq, KQuery)
        assert kq.redis_key == keys[ctr]
        assert kq.query == {'hello': 'goodbye'}  # see testing.MockRedisPipeline
        assert kq.cached_data == kq.query
        ctr += 1
    assert redis_cli.set_call_count == 0 and redis_cli.get_call_count == 0
    assert redis_cli.derived_pipeline.pipe_set_call_count == 0


@patch('tscached.kquery.query_kairos', autospec=True)
def test_proxy_to_kairos(m_query_kairos):
    m_query_kairos.return_value = {'queries': [{'name': 'first'}, {'name', 'second'}]}

    kq = KQuery(MockRedis())
    kq.query = {'hello': 'goodbye'}
    time_range = {'start_absolute': 1234567890000}
    kq.proxy_to_kairos('localhost', 8080, time_range)

    called_query = {'metrics': [{'hello': 'goodbye'}], 'cache_time': 0, 'start_absolute': 1234567890000}
    m_query_kairos.assert_called_once_with('localhost', 8080, called_query)


@freeze_time("2016-01-01 00:00:00", tz_offset=-8)
@patch('tscached.kquery.query_kairos', autospec=True)
def test_proxy_to_kairos_chunked_happy(m_query_kairos):
    m_query_kairos.return_value = {'queries': [{'name': 'first'}, {'name', 'second'}]}

    kq = KQuery(MockRedis())
    kq.query = {'hello': 'goodbye'}
    then = datetime.datetime.fromtimestamp(1234567890)
    diff = datetime.timedelta(minutes=30)
    time_ranges = [(then - diff, then), (then - diff - diff, then - diff)]
    results = kq.proxy_to_kairos_chunked('localhost', 8080, time_ranges)

    assert len(results) == 2
    assert m_query_kairos.call_count == 2
    expected_query = {'cache_time': 0, 'metrics': [{'hello': 'goodbye'}]}

    expected_query['start_absolute'] = int((then - diff).strftime('%s')) * 1000
    expected_query['end_absolute'] = int((then).strftime('%s')) * 1000
    assert m_query_kairos.call_args_list[0] == (('localhost', 8080, expected_query), {'propagate': False})

    expected_query['start_absolute'] = int((then - diff - diff).strftime('%s')) * 1000
    expected_query['end_absolute'] = int((then - diff).strftime('%s')) * 1000
    assert m_query_kairos.call_args_list[1] == (('localhost', 8080, expected_query), {'propagate': False})


@freeze_time("2016-01-01 00:00:00", tz_offset=-8)
@patch('tscached.kquery.query_kairos', autospec=True)
def test_proxy_to_kairos_chunked_raises_except(m_query_kairos):
    m_query_kairos.return_value = {'error': 'some error message', 'status_code': 500}

    kq = KQuery(MockRedis())
    kq.query = {'hello': 'goodbye'}
    then = datetime.datetime.fromtimestamp(1234567890)
    diff = datetime.timedelta(minutes=30)
    time_ranges = [(then - diff, then), (then - diff - diff, then - diff)]
    with pytest.raises(BackendQueryFailure):
        kq.proxy_to_kairos_chunked('localhost', 8080, time_ranges)


@freeze_time("2016-01-01 00:00:00", tz_offset=-8)
def test_upsert():

    class FakeMTS():
        def get_key(self):
            return 'rick-and-morty'

    redis_cli = MockRedis()
    kq = KQuery(redis_cli)
    kq.query = {'hello': 'some_query'}
    kq.add_mts(FakeMTS())
    kq.upsert(datetime.datetime.fromtimestamp(1234567890), None)
    assert redis_cli.set_call_count == 1
    assert redis_cli.get_call_count == 0
    assert kq.query['mts_keys'] == ['rick-and-morty']
    assert kq.query['last_add_data'] == time.time()
    assert kq.query['earliest_data'] == 1234567890
    assert sorted(kq.query.keys()) == ['earliest_data', 'hello', 'last_add_data', 'mts_keys']

    kq.upsert(datetime.datetime.fromtimestamp(1234567890), datetime.datetime.fromtimestamp(1234569890))
    assert redis_cli.set_call_count == 2
    assert redis_cli.get_call_count == 0
    assert kq.query['last_add_data'] == 1234569890
    assert kq.query['earliest_data'] == 1234567890
