"""Independent, plainly labelled reviewer assessments; never changes signals."""
import json
from pathlib import Path
from urllib.parse import urlparse
import config
import news_feed

LABELS = {'highly_positive': 'Very positive', 'positive': 'Positive',
          'neutral': 'Neutral', 'negative': 'Negative', 'highly_negative': 'Very negative'}


def _read(name):
    """The newer of the bundled file and main's live copy. The dashboard runs
    from a frozen deploy branch, so the bundled copy alone goes stale."""
    import remote_data
    try:
        local = json.loads((Path(config.BASE_DIR) / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        local = None
    return remote_data.newer(local, remote_data.fetch_json(name, 'main'))


def load(name):
    try:
        data = _read(name)
        if data is None:
            raise ValueError('No ratings file')
        stamp, age = news_feed._fresh_as_of(data)
        if stamp is None:
            return {}, {'status': age}
        ratings = data.get('ratings', {})
        if not isinstance(ratings, dict):
            raise ValueError('Invalid ratings')
        valid = {s: r for s, r in ratings.items() if s in config.STOCKS
                 and news_feed._valid_scoring_rating(r) and isinstance(r.get('reason'), str)}
        return valid, {**data, 'status': 'ok', 'age_hours': age}
    except (OSError, ValueError, TypeError):
        return {}, {'status': 'unavailable'}


def _status_line(name, meta):
    if meta['status'] == 'ok':
        return f"{name} reviewed {meta['as_of']} · {meta['age_hours']:.1f} hours ago"
    # A stale or unreadable review is named, never shown as current.
    return f"{name} review: {meta['status']} -- its ratings are not shown as current"


def show(st):
    codex, meta = load('news_codex_ratings.json')
    claude, other = load('news_ai_ratings.json')
    other_name = other.get('provider', 'Claude') if other['status'] == 'ok' else 'Claude'
    st.markdown('### News assessment desk')
    st.caption('News impact and trading signals answer different questions. A positive story does not automatically make a stock a Buy. No reviewed news is not a Neutral rating.')
    # Each reviewer stands alone. Until 2026-10-02 a stale Codex review
    # returned here early and also hid Claude's fresh ratings for 8 days.
    st.caption(_status_line('Codex', meta) + '  \n' + _status_line(other_name, other))
    if meta['status'] != 'ok' and other['status'] != 'ok':
        st.info('No fresh news review is available. The headlines below are still current.')
        return
    primary = codex if meta['status'] == 'ok' else claude
    columns = st.columns(4)
    for col, label, values in zip(columns, ('Positive', 'Negative', 'Neutral', 'Not reviewed'),
                                  (('positive','highly_positive'), ('negative','highly_negative'), ('neutral',), ())):
        count = sum(r['rating'] in values for r in primary.values()) if values else len(config.STOCKS)-len(primary)
        col.metric(label, count)
    if meta['status'] == 'ok':
        for note in meta.get('limitations', []):
            st.info(note)
    codex_none = 'No reviewed news' if meta['status'] == 'ok' else f"Review {meta['status']}"
    other_none = 'No fresh review' if other['status'] == 'ok' else f"Review {other['status']}"
    reviewed = set(codex) | set(claude)
    st.dataframe([{'Stock': s, 'Codex': LABELS.get(codex.get(s, {}).get('rating'), codex_none),
                   other_name: LABELS.get(claude.get(s, {}).get('rating'), other_none),
                   'Stock connection': (codex.get(s) or claude.get(s) or {}).get('relevance', 'Not assessed'),
                   'Why it matters': (codex.get(s) or claude.get(s) or {}).get('reason', 'No stock-specific assessment in this review.')}
                  for s in sorted(config.STOCKS, key=lambda s: (s not in reviewed, s))],
                 hide_index=True, width='stretch')
    if meta['status'] == 'ok':
        for context in meta.get('market_context', []):
            with st.expander(context['topic'] + ' · ' + context['impact']):
                st.write(context['reason'])
                st.link_button('Read market context', context['source'])
    symbol = st.selectbox('Read the evidence for a stock', sorted(reviewed) or sorted(config.STOCKS), key='news_desk_stock')
    for title, ratings in (('Codex', codex), (other_name, claude)):
        rating = ratings.get(symbol)
        if not rating:
            continue
        with st.container(border=True):
            st.markdown(f"**{symbol} · {title} · {LABELS.get(rating['rating'], 'Unrated')}**")
            st.write(rating['reason'])
            st.caption(f"Evidence: {rating.get('text_depth', 'not specified')} · Reviewer's confidence: {rating['confidence']:.0%} (not a return probability)")
            for url in rating['sources']:
                parsed = urlparse(url)
                if parsed.scheme == 'https' and parsed.hostname:
                    st.link_button('Read source · ' + parsed.hostname, url)
