"""Independent, plainly labelled reviewer assessments; never changes signals."""
import json
from pathlib import Path
from urllib.parse import urlparse
import config
import news_feed

LABELS = {'highly_positive': 'Very positive', 'positive': 'Positive',
          'neutral': 'Neutral', 'negative': 'Negative', 'highly_negative': 'Very negative'}


def load(name):
    try:
        data = json.loads((Path(config.BASE_DIR) / name).read_text(encoding='utf-8'))
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


def show(st):
    codex, meta = load('news_codex_ratings.json')
    claude, other = load('news_ai_ratings.json')
    st.markdown('### News assessment desk')
    st.caption('News impact and trading signals answer different questions. A positive story does not automatically make a stock a Buy. No reviewed news is not a Neutral rating.')
    if meta['status'] != 'ok':
        st.info(f"Codex review: {meta['status']}. Existing reviews and news remain below.")
        return
    st.caption(f"Codex reviewed {meta['as_of']} · {meta['age_hours']:.1f} hours ago · {meta.get('review_mode', 'Scheduled review')}")
    columns = st.columns(4)
    for col, label, values in zip(columns, ('Positive', 'Negative', 'Neutral', 'Not reviewed'),
                                  (('positive','highly_positive'), ('negative','highly_negative'), ('neutral',), ())):
        count = sum(r['rating'] in values for r in codex.values()) if values else len(config.STOCKS)-len(codex)
        col.metric(label, count)
    for note in meta.get('limitations', []):
        st.info(note)
    st.dataframe([{'Stock': s, 'Codex': LABELS.get(codex.get(s, {}).get('rating'), 'No reviewed news'),
                   'Other reviewer': LABELS.get(claude.get(s, {}).get('rating'), 'No fresh review'),
                   'Stock connection': codex.get(s, {}).get('relevance', 'Not assessed'),
                   'Why it matters': codex.get(s, {}).get('reason', 'No stock-specific assessment in this review.')}
                  for s in sorted(config.STOCKS, key=lambda s: (s not in codex, s))],
                 hide_index=True, width='stretch')
    for context in meta.get('market_context', []):
        with st.expander(context['topic'] + ' · ' + context['impact']):
            st.write(context['reason'])
            st.link_button('Read market context', context['source'])
    symbol = st.selectbox('Read the evidence for a stock', sorted(codex) or sorted(config.STOCKS), key='news_desk_stock')
    for title, ratings in (('Codex', codex), (other.get('provider', 'Other reviewer'), claude)):
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
