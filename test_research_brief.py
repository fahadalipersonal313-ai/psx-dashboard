"""The brief may summarize evidence, but must never manufacture its timing."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

import research_brief as brief
import research_contract as contract
import research_desk as desk
from test_research_contract import fixture, technical, quote


def at(value):
    return contract.stamp(value)


def context(asof='2026-10-05T09:00:00+05:00', generated=None, expires=None):
    value = fixture()
    value.update(as_of=asof, generated_at=generated or asof,
                 expires_at=expires or (at(generated or asof) + timedelta(hours=48)).isoformat())
    value['sources'][0].update(published_at=asof, verified_at=asof)
    for row in value['stocks']:
        row['fundamentals'].update(reviewed_at=asof, next_review_at=(at(asof) + timedelta(days=30)).isoformat())
    return value


def built(value, now, activity=None):
    return brief.build(desk.build(value, now=at(now)), activity, now=at(now))


def source(value, sid, published=None, day=None, url=None, **kwargs):
    result = {'id': sid, 'title': sid + ' source', 'kind': 'issuer_filing',
              'url': url or 'https://example.org/' + sid, 'verified_at': value['as_of']}
    if published is not None:
        result['published_at'] = published
    if day is not None:
        result.update(published_date=day, publication_precision='date')
    result.update(kwargs)
    value['sources'].append(result)
    return result


def event(value, symbol='PRL', day='2026-10-06', kind='earnings'):
    item = {'date': day, 'kind': kind, 'known_at': value['as_of'], 'source_ids': ['s']}
    next(r for r in value['stocks'] if r['symbol'] == symbol)['fundamentals']['events'] = [item]
    return item


def activity(recorded='2026-10-05T08:00:00+05:00', checked='2026-10-05T09:00:00+05:00'):
    item = {'id': 'a', 'symbol': 'PRL', 'recorded_at': recorded, 'checkpoint_id': 'checkpoint-a',
            'changed_fields': ['company_review'], 'before': {'company_review': 'Old review'},
            'after': {'company_review': 'Changed review'},
            'title': 'A newly published company event', 'detail': 'This is not source evidence'}
    return {'schema_version': 1, 'checked_at': checked, 'items': [item]}


class ResearchBriefTests(unittest.TestCase):
    def test_weekend_review_does_not_claim_monday_overnight_coverage(self):
        value = context('2026-10-03T10:00:00+05:00', generated='2026-10-04T14:00:00+05:00')
        source(value, 'sat', '2026-10-03T08:30:00+05:00')
        result = built(value, '2026-10-04T18:00:00+05:00')
        self.assertEqual(result['status'], 'cached_prior_review')
        self.assertEqual(result['label'], 'Cached prior review / overnight changes not verified')
        self.assertEqual(result['target_session'], '2026-10-05')
        self.assertEqual(result['prior_close'], '2026-10-02T16:30:00+05:00')
        self.assertEqual(result['target_open'], '2026-10-05T09:32:00+05:00')
        self.assertFalse(result['current_session_review'])
        self.assertEqual(result['overnight_status'], 'not_verified')
        self.assertEqual(result['overnight_publications'], [])
        self.assertEqual(result['as_of'], value['as_of'])

    def test_generation_on_target_day_cannot_renew_saturday_review(self):
        value = context('2026-10-03T10:00:00+05:00', generated='2026-10-05T09:00:00+05:00')
        result = built(value, '2026-10-05T09:10:00+05:00')
        self.assertTrue(result['research_current'])
        self.assertEqual(result['status'], 'cached_prior_review')
        self.assertFalse(result['current_session_review'])

    def test_same_day_preopen_review_and_original_source_times(self):
        value = context()
        source(value, 'overnight', '2026-10-02T11:31:00Z')
        result = built(value, '2026-10-05T09:10:00+05:00')
        self.assertEqual(result['status'], 'current_preopen_review')
        self.assertTrue(result['current_session_review'])
        item = next(r for r in result['overnight_publications'] if r['title'] == 'overnight source')
        self.assertEqual(item['published_at'], '2026-10-02T11:31:00Z')
        self.assertEqual(item['source_refs'][0]['verified_at'], value['as_of'])
        for key in ('as_of', 'expires_at', 'target_session', 'target_open', 'prior_close'):
            self.assertEqual(item[key], result[key])

    def test_after_open_review_is_current_update_not_morning_review(self):
        value = context('2026-10-05T11:00:00+05:00')
        source(value, 'preopen', '2026-10-05T09:10:00+05:00')
        result = built(value, '2026-10-05T11:05:00+05:00')
        self.assertEqual(result['status'], 'current_session_update')
        self.assertEqual([p['title'] for p in result['overnight_publications']], ['preopen source'])
        self.assertIn('not a pre-open review', result['label'])

    def test_friday_lunch_remains_friday_and_has_two_segments(self):
        value = context('2026-10-02T09:00:00+05:00')
        result = built(value, '2026-10-02T13:00:00+05:00')
        self.assertEqual(result['target_session'], '2026-10-02')
        self.assertEqual(result['phase'], 'session_break')
        self.assertEqual(result['prior_close'], '2026-10-01T15:30:00+05:00')
        self.assertEqual(result['target_segments'], [
            {'open': '2026-10-02T09:17:00+05:00', 'close': '2026-10-02T12:00:00+05:00'},
            {'open': '2026-10-02T14:32:00+05:00', 'close': '2026-10-02T16:30:00+05:00'}])
        self.assertFalse(any(r['kind'] == 'existing_swing_setup' for r in result['attention']))

    def test_friday_at_and_after_close_targets_monday_without_delay(self):
        for clock in ('16:30:00', '16:31:00', '23:00:00'):
            with self.subTest(clock=clock):
                value = context('2026-10-02T15:00:00+05:00')
                result = built(value, '2026-10-02T' + clock + '+05:00')
                self.assertEqual(result['target_session'], '2026-10-05')
                self.assertEqual(result['prior_close'], '2026-10-02T16:30:00+05:00')
                self.assertEqual(result['status'], 'cached_prior_review')

    def test_noticed_holiday_is_skipped(self):
        value = context('2026-11-09T08:00:00+05:00')
        result = built(value, '2026-11-09T09:00:00+05:00')
        self.assertEqual(result['target_session'], '2026-11-10')
        self.assertEqual(result['prior_session'], '2026-11-06')
        self.assertEqual(result['status'], 'cached_prior_review')

    def test_expired_missing_invalid_and_future_reviews_fail_closed(self):
        now = '2026-10-05T09:10:00+05:00'
        expired = context(expires=now)
        future = context('2026-10-05T09:11:00+05:00')
        invalid = context(); invalid['schema_version'] = 999
        for value, status in [(expired, 'expired'), (None, 'missing'), (future, 'future'), (invalid, 'invalid')]:
            with self.subTest(status=status):
                # Raw fake desk data cannot override review validation.
                result = brief.build({'context': value, 'research_current': True, 'rows': []}, activity(), now=at(now))
                self.assertEqual(result['status'], status)
                self.assertFalse(result['current_session_review'])
                self.assertEqual(result['attention'], [])
                self.assertEqual(result['overnight_publications'], [])
                self.assertEqual(result['condition_changes']['items'], [])
                self.assertEqual(result['coverage']['unavailable_financial_reviews'], 15)

    def test_unknown_calendar_fails_closed_including_manifest_edges(self):
        for now in ('2027-01-04T09:00:00+05:00', '2026-08-03T09:00:00+05:00', '2026-12-31T16:00:00+05:00'):
            with self.subTest(now=now):
                result = built(context(now), now)
                self.assertEqual(result['status'], 'calendar_unavailable')
                self.assertIsNone(result['target_session'])
                self.assertEqual(result['attention'], [])
                self.assertEqual(result['overnight_publications'], [])

    def test_explicit_aware_evaluation_time_is_required(self):
        for now in (None, datetime(2026, 10, 5, 9, 0), '2026-10-05T09:00:00+05:00'):
            with self.assertRaises(ValueError):
                brief.build({}, now=now)

    def test_date_only_is_separate_and_cannot_prove_after_close(self):
        value = context()
        source(value, 'date-only-prior-close-day', day='2026-10-02')
        source(value, 'date-only-monday', day='2026-10-05')
        source(value, 'too-old', day='2026-10-01')
        result = built(value, '2026-10-05T09:10:00+05:00')
        self.assertEqual(len(result['publication_time_unknown']), 2)
        self.assertTrue(all(p['published_at'] is None for p in result['publication_time_unknown']))
        self.assertTrue(all('not verified as overnight' in p['timing'] for p in result['publication_time_unknown']))
        self.assertFalse(any('date-only' in p['title'] for p in result['overnight_publications']))
        self.assertEqual(result['coverage']['sources_without_publication_time'], 3)

    def test_publication_window_boundaries_and_deduplication(self):
        value = context('2026-10-05T10:00:00+05:00')
        source(value, 'before-close', '2026-10-02T16:29:00+05:00')
        source(value, 'at-close', '2026-10-02T16:30:00+05:00')
        source(value, 'after-close', '2026-10-02T16:31:00+05:00', url='https://example.org/notice')
        source(value, 'duplicate', '2026-10-02T11:31:00Z', url='https://example.org/notice#section')
        source(value, 'at-open', '2026-10-05T09:32:00+05:00')
        source(value, 'after-open', '2026-10-05T09:33:00+05:00')
        result = built(value, '2026-10-05T10:05:00+05:00')
        self.assertEqual(len(result['overnight_publications']), 1)
        self.assertEqual(len(result['overnight_publications'][0]['source_refs']), 2)

    def test_future_publication_or_date_only_with_invented_time_invalidates_context(self):
        for params in ({'published': '2026-10-05T09:30:00+05:00'}, {'day': '2026-10-06'},
                       {'published': '2026-10-05T08:00:00+05:00', 'day': '2026-10-05'}):
            value = context()
            source(value, 'bad', **params)
            result = brief.build({'context': value}, now=at('2026-10-05T09:10:00+05:00'))
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['overnight_publications'], [])

    def test_activity_is_a_condition_change_never_new_company_event(self):
        value, payload = context(), activity()
        payload['items'].append({**payload['items'][0], 'id': 'other-id'})
        payload['items'].append({**payload['items'][0], 'id': 'future', 'recorded_at': '2026-10-05T09:01:00+05:00'})
        payload['items'].append({'not': 'a valid record'})
        result = built(value, '2026-10-05T09:10:00+05:00', payload)
        changes = result['condition_changes']
        self.assertEqual(len(changes['items']), 1)
        self.assertEqual(changes['excluded_items'], 2)
        item = changes['items'][0]
        self.assertEqual(item['title'], 'PRL: dashboard conditions changed')
        self.assertEqual(item['source_refs'][0]['artifact'], 'research_activity.json')
        self.assertEqual(item['recorded_at'], payload['items'][0]['recorded_at'])
        self.assertNotIn('newly published', str(item))
        self.assertNotIn('https://example.org', str(item['source_refs']))

    def test_future_activity_checkpoint_and_prior_close_changes_are_withheld(self):
        result = built(context(), '2026-10-05T09:10:00+05:00', activity(checked='2026-10-05T10:00:00+05:00'))
        self.assertEqual(result['condition_changes']['status'], 'unavailable')
        result = built(context(), '2026-10-05T09:10:00+05:00', activity(recorded='2026-10-02T16:30:00+05:00'))
        self.assertEqual(result['condition_changes']['items'], [])

    def test_thirty_day_old_empty_activity_is_not_current_window_coverage(self):
        now = at('2026-10-05T09:10:00+05:00')
        old = (now - timedelta(days=30)).isoformat()
        payload = {'schema_version': 1, 'checked_at': old, 'items': []}
        result = built(context(), now.isoformat(), payload)
        changes = result['condition_changes']
        self.assertEqual(changes['status'], 'stale')
        self.assertEqual(changes['items'], [])
        self.assertEqual(changes['checked_at'], old)
        self.assertEqual(changes['checked_age_seconds'], 30 * 86400)
        self.assertEqual(changes['range_coverage'], 'unverified')
        self.assertEqual(changes['window_start'], result['prior_close'])

    def test_recent_empty_activity_still_does_not_prove_complete_range(self):
        payload = activity(); payload['items'] = []
        result = built(context(), '2026-10-05T09:10:00+05:00', payload)
        changes = result['condition_changes']
        self.assertEqual(changes['status'], 'available')
        self.assertEqual(changes['checked_age_seconds'], 600)
        self.assertEqual(changes['range_coverage'], 'unverified')
        self.assertIn('starting checkpoint', changes['coverage_note'])
        self.assertIn('zero listed items does not verify no changes', changes['coverage_note'])

    def test_activity_timestamp_remains_visible_when_research_withheld(self):
        payload = activity()
        result = built(None, '2026-10-05T09:10:00+05:00', payload)
        self.assertEqual(result['condition_changes']['status'], 'withheld')
        self.assertEqual(result['condition_changes']['items'], [])
        self.assertEqual(result['condition_changes']['checked_at'], payload['checked_at'])
        self.assertEqual(result['condition_changes']['checked_age_seconds'], 600)

    def test_existing_live_swing_state_is_not_new_rank_or_forecast(self):
        now = at('2026-10-05T11:00:00+05:00')
        value = context(now.isoformat())
        current = desk.build(value, {'rows': [technical()]}, {'prices': [quote()]}, now=now)
        result = brief.build(current, now=now)
        self.assertEqual([i['symbol'] for i in result['attention']], ['PRL'])
        item = result['attention'][0]
        self.assertEqual(item['kind'], 'existing_swing_setup')
        self.assertEqual(item['quote_as_of'], quote()['source_as_of'])
        self.assertTrue(item['source_refs'])
        self.assertNotIn('score', item)
        # Rerendering later must not preserve a formerly fresh quote/setup.
        stale = brief.build(current, now=now + timedelta(minutes=21))
        self.assertEqual(stale['attention'], [])
        current['rows'][0]['swing_state'] = 'Conditional swing plan'
        self.assertEqual(brief.build(current, now=now)['attention'], [])

    def test_rederivation_rejects_changed_technical_or_research_guards(self):
        now = at('2026-10-05T11:00:00+05:00')
        for guard in ('technical', 'research'):
            with self.subTest(guard=guard):
                value = context(now.isoformat())
                current = desk.build(value, {'rows': [technical()]}, {'prices': [quote()]}, now=now)
                self.assertEqual(current['rows'][0]['swing_state'], 'Swing setup for review')
                if guard == 'technical':
                    current['rows'][0]['technical']['config_hash'] = 'obsolete'
                else:
                    current['context']['stocks'][0]['news']['bias'] = 'adverse'
                self.assertEqual(brief.build(current, now=now)['attention'], [])

    def test_prior_preopen_review_stays_dated_after_open(self):
        result = built(context(), '2026-10-05T11:00:00+05:00')
        self.assertEqual(result['status'], 'current_preopen_review')
        self.assertIn('09:00 AM PKT', result['label'])
        self.assertIn('later changes not verified', result['label'])
        self.assertEqual(result['attention'], [])

    def test_cached_review_cannot_promote_forged_ready_state(self):
        now = at('2026-10-05T11:00:00+05:00')
        value = context('2026-10-03T10:00:00+05:00', generated=now.isoformat())
        current = desk.build(value, now=now)
        current['rows'][0].update(swing_state='Swing setup for review', plan={'reference_entry': 100}, quote=quote())
        self.assertEqual(brief.build(current, now=now)['attention'], [])

    def test_sourced_near_event_can_remain_clearly_cached(self):
        value = context('2026-10-03T10:00:00+05:00', generated='2026-10-04T14:00:00+05:00')
        listed = event(value)
        value['stocks'][0]['fundamentals']['events'].append(deepcopy(listed))
        event(value, symbol='SYS', day='2026-10-11')  # Six days from Monday.
        result = built(value, '2026-10-04T18:00:00+05:00')
        self.assertEqual(len(result['attention']), 1)
        item = result['attention'][0]
        self.assertEqual(item['review_status'], 'cached_prior_review')
        self.assertEqual(item['known_at'], listed['known_at'])
        self.assertTrue(item['source_refs'])
        self.assertEqual(item['event_date'], '2026-10-06')

    def test_future_or_unsourced_listed_events_are_not_attention(self):
        for field, value in [('known_at', '2026-10-05T10:00:00+05:00'), ('source_ids', [])]:
            v = context(); e = event(v); e[field] = value
            result = brief.build({'context': v}, now=at('2026-10-05T09:10:00+05:00'))
            self.assertEqual(result['status'], 'invalid')
            self.assertEqual(result['attention'], [])

    def test_missing_reviews_and_sources_have_explicit_counts(self):
        value = context()
        value['stocks'][0]['fundamentals'].update(status='unavailable', source_ids=[])
        value['stocks'][1]['news'].update(status='unavailable', source_ids=[])
        result = built(value, '2026-10-05T09:10:00+05:00')
        counts = result['coverage']
        self.assertEqual(counts['unavailable_public_sentiment'], 15)
        self.assertEqual(counts['unavailable_financial_reviews'], 1)
        self.assertEqual(counts['unavailable_company_news'], 1)
        self.assertEqual(counts['components_without_sources'], 17)

    def test_deterministic_and_does_not_mutate_inputs(self):
        value, payload = context(), activity()
        event(value)
        now = at('2026-10-05T09:10:00+05:00')
        current = desk.build(value, now=now)
        original = deepcopy((current, payload))
        one = brief.build(current, payload, now=now)
        self.assertEqual(one, brief.build(current, payload, now=now))
        self.assertEqual((current, payload), original)
        one['attention'][0]['source_refs'][0]['title'] = 'changed'
        one['condition_changes']['items'][0]['after']['company_review'] = 'changed'
        self.assertEqual((current, payload), original)

    def test_malformed_interval_is_unavailable(self):
        with patch('session_calendar.intervals', return_value=[('15:00', '09:00')]):
            result = brief.build({'context': context()}, now=at('2026-10-05T09:10:00+05:00'))
        self.assertEqual(result['status'], 'calendar_unavailable')


class RendererTests(unittest.TestCase):
    def test_render_includes_missingness_and_does_not_fetch(self):
        class UI:
            def __init__(self): self.text = []
            def __getattr__(self, name):
                def render(*values):
                    self.text.extend(str(value) for value in values)
                    return self
                return render
            def __enter__(self): return self
            def __exit__(self, *args): return False
        ui = UI()
        result = built(context(), '2026-10-05T09:10:00+05:00')
        brief.show(ui, result)
        text = ' '.join(ui.text)
        self.assertIn('Unavailable public-sentiment reviews: 15/15', text)
        self.assertIn('evidence components without sources', text)
        self.assertIn('not verified source-publication', text)
        self.assertIn('does not fetch new sources', text)
        self.assertIn('Activity checkpoint through: unavailable', text)
        self.assertIn('zero listed items does not verify no changes', text)
        ui.text.clear()
        payload = activity(checked='2026-09-05T09:10:00+05:00'); payload['items'] = []
        brief.show(ui, built(context(), '2026-10-05T09:10:00+05:00', payload))
        text = ' '.join(ui.text)
        self.assertIn('Activity checkpoint through: Sat 05 Sep 2026, 09:10 AM PKT', text)
        self.assertIn('30.0 days old', text)
        self.assertIn('predates the prior close', text)


if __name__ == '__main__':
    unittest.main()
