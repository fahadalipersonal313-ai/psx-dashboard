"""Plain-language operating rules and evidence, without promising returns."""
import config
import database as db


def guards():
    return [
        {'Check': 'Wider market falling', 'Mode': 'Blocks Buy' if config.REGIME_GATE_ENABLED else 'Warning only', 'Why': 'Strong stocks can still rise in a weak market.'},
        {'Check': 'Missing or invalid prices', 'Mode': 'Required', 'Why': 'A buying call needs verified prices.'},
        {'Check': 'Shariah eligibility', 'Mode': 'Required', 'Why': 'Only verified eligible stocks qualify.'},
        {'Check': 'Trading value and volume', 'Mode': 'Required', 'Why': 'Avoid stocks that may be hard to enter or exit.'},
        {'Check': 'Price broke below support', 'Mode': 'Required', 'Why': 'A cheaper price alone is not a recovery signal.'},
        {'Check': 'Stock momentum and buying flow', 'Mode': 'Required', 'Why': 'Keep evidence of strength in the stock itself.'},
        {'Check': 'Stop and target order', 'Mode': 'Required', 'Why': 'The loss limit must be below entry and the target above it.'},
        {'Check': 'Sudden price jump', 'Mode': 'Wait one session', 'Why': 'A news-driven jump can reverse; reassess after a completed session.'},
        {'Check': 'Strong Buy confirmation', 'Mode': 'Two completed sessions', 'Why': 'Repeated polling is not fresh confirmation.'},
        {'Check': 'Small score changes', 'Mode': f'{config.HYSTERESIS_BAND}-point buffer', 'Why': 'Avoid repeated changes near a score boundary.'},
        {'Check': 'Price far above recent trend', 'Mode': 'On' if config.CHASE_GUARD_ENABLED else 'Off', 'Why': 'Previously disabled; not switched back on.'},
        {'Check': 'Target close to resistance', 'Mode': 'On' if config.POOR_RR_VETO_ENABLED else 'Off', 'Why': 'Target and loss limit are still displayed.'},
        {'Check': 'Single-stock signal concentration veto', 'Mode': 'On' if config.CONCENTRATION_VETO_ENABLED else 'Off', 'Why': 'Account-level cash, position and sector limits still apply.'},
        {'Check': 'News and order depth', 'Mode': 'Context only', 'Why': 'No measured evidence yet for changing the daily buying score.'},
    ]


def evidence():
    import decision_engine
    contract = decision_engine.digest(decision_engine.contract())
    rows = [r for r in db.cohort_summary() if r['version'] == config.STRATEGY_VERSION and r['config_hash'] == contract]
    completed = sum(r['n'] for r in rows if r['status'] in ('target', 'stop', 'expired'))
    unresolved = sum(r['n'] for r in rows if r['status'] in ('pending', 'unavailable'))
    return {'completed': completed, 'unresolved': unresolved}


def show(st, runs):
    with st.expander('Buying checks and how much to trust the results'):
        st.dataframe(guards(), hide_index=True)
        st.caption('Buying more lowers the average price but increases the money at risk. Require a fresh stock-specific reason and an affordable loss limit; a loss by itself is not a reason to add.')
        stale_rules = sum(r.get('strategy_version') != config.STRATEGY_VERSION for r in runs)
        if stale_rules:
            st.warning(f'{stale_rules} displayed stocks still use earlier rules. Wait for the next engine update; their saved signals have not been relabelled.')
        info = evidence()
        st.write(f"Current-rule candidate checks: {info['completed']} completed; {info['unresolved']} waiting or missing prices.")
        st.info('History checks help review past decisions. They do not establish that this is the best strategy or that it will make money. Candidate checks include rejected ideas, not just Buy calls.')
        st.caption('The falling-market block is off. Daily calls still use completed sessions. News can explain a rally, but a headline alone does not override missing prices, weak liquidity or broken support.')
