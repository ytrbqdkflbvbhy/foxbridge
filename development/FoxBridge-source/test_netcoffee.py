import json
import unittest

from netcoffee import NetCoffeeError, collect_statistics, score_from_risk


class NetCoffeeTests(unittest.TestCase):
    def fake(self, *, risk_error=None, geo_error=None, risk=None, trace_ip='8.8.8.8'):
        calls = []
        def get(host, path):
            calls.append((host, path))
            if host == 'claude.ai':
                return f'ip={trace_ip}\nloc=DE\ncolo=MUC\n'.encode()
            if '/geoip/' in path:
                if geo_error:
                    raise geo_error
                return json.dumps({'country': 'Germany', 'city': 'Munich', 'country_code': 'de'}).encode()
            if '/iprisk/' in path:
                if risk_error:
                    raise risk_error
                return json.dumps(risk if risk is not None else {
                    'ip': trace_ip, 'countryCode': 'de', 'trust_score': 83, 'is_datacenter': True}).encode()
            raise AssertionError('Unexpected endpoint')
        return get, calls

    def test_trace_ip_is_used_for_both_public_get_endpoints(self):
        get, calls = self.fake()
        result = collect_statistics(8765, None, get)
        self.assertEqual(result['score'], 83)
        self.assertEqual(result['ip'], '8.8.8.8')
        self.assertEqual(result['errors'], {})
        self.assertEqual(set(calls), {
            ('claude.ai', '/cdn-cgi/trace'), ('ip.net.coffee', '/api/geoip/8.8.8.8'),
            ('ip.net.coffee', '/api/iprisk/8.8.8.8')})

    def test_missing_invalid_and_zero_scores_are_distinct(self):
        for value in (None, True, -1, 101, '83', float('nan')):
            self.assertIsNone(score_from_risk({'trust_score': value})[0])
        self.assertEqual(score_from_risk({'trust_score': 0})[0], 0)

    def test_site_regional_rule_preserves_original_score(self):
        score, raw, adjusted = score_from_risk({'countryCode': 'ru', 'trust_score': 97})
        self.assertEqual((score, raw, adjusted), (0, 97, True))

    def test_geo_failure_does_not_hide_a_valid_score(self):
        get, _ = self.fake(geo_error=NetCoffeeError('geo unavailable'))
        result = collect_statistics(8765, None, get)
        self.assertEqual(result['score'], 83)
        self.assertIsNone(result['geoip'])
        self.assertIn('geoip', result['errors'])

    def test_score_failure_retains_partial_data_and_backoff(self):
        get, _ = self.fake(risk_error=NetCoffeeError('HTTP 429', 90))
        result = collect_statistics(8765, None, get)
        self.assertIsNone(result['score'])
        self.assertEqual(result['geoip']['city'], 'Munich')
        self.assertEqual(result['retry_after'], 90)

    def test_rejects_score_for_a_different_ip(self):
        get, _ = self.fake(risk={'ip': '1.1.1.1', 'trust_score': 100})
        result = collect_statistics(8765, None, get)
        self.assertIsNone(result['score'])
        self.assertIn('iprisk', result['errors'])

    def test_rejects_invalid_trace_before_querying_third_party(self):
        for value in ('127.0.0.1', 'not-an-ip', ''):
            get, calls = self.fake(trace_ip=value)
            with self.assertRaises(NetCoffeeError):
                collect_statistics(8765, None, get)
            self.assertEqual(len(calls), 1)

    def test_ipv6_addresses_are_compared_by_value(self):
        get, _ = self.fake(trace_ip='2001:4860:4860::8888', risk={
            'ip': '2001:4860:4860:0:0:0:0:8888', 'trust_score': 83})
        self.assertEqual(collect_statistics(8765, None, get)['score'], 83)

    def test_cached_representative_is_accepted_only_with_a_matching_cidr(self):
        get, _ = self.fake(trace_ip='2001:4860:4860::8888', risk={
            'ip': '2001:4860:4860::8844', 'cidr': '2001:4860:4860::/48', 'trust_score': 91})
        result = collect_statistics(8765, None, get)
        self.assertEqual(result['score'], 91)
        self.assertEqual(result['ip'], '2001:4860:4860::8888')
        self.assertEqual(result['score_scope'], 'cidr')


if __name__ == '__main__':
    unittest.main()
