"""Stateless expected-score prototype; coefficients are editable priors."""
from __future__ import annotations
import copy
import math
from statistics import NormalDist

NORMAL = NormalDist()
METRICS = ('y', 'dy', 's', 'ds', 'x', 'dx', 'p', 't')


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('teams'), dict) or not isinstance(data.get('games'), list):
        raise ValueError('Snapshot needs teams (object) and games (list).')
    par = data.get('parameters', {})
    for key in ('by', 'bs', 'shrink', 'turnover_retention', 'turnover_point_value'):
        if not isinstance(par.get(key), (int, float)) or not math.isfinite(par[key]):
            raise ValueError(f'Missing or invalid parameter: {key}')
    for league in ('NFL', 'CFB'):
        for key in ('q', 'n', 'y', 's', 'x', 'bx', 'h', 'sigma_spread', 'sigma_total'):
            if not isinstance(par.get(league, {}).get(key), (int, float)) or not math.isfinite(par[league][key]):
                raise ValueError(f'Missing or invalid parameter: {league}.{key}')
        if min(par[league]['n'], par[league]['sigma_spread'], par[league]['sigma_total']) <= 0:
            raise ValueError('Reference plays and residual sigmas must be positive.')
    for name, team in data['teams'].items():
        if team.get('league') not in ('NFL', 'CFB'):
            raise ValueError(f'Invalid league for {name}')
        for key in METRICS:
            if team.get(key) is not None and (not isinstance(team[key], (int, float)) or not math.isfinite(team[key])):
                raise ValueError(f'Invalid team metric {name}.{key}')
    for game in data['games']:
        if not isinstance(game, dict) or not all(isinstance(game.get(k), str) for k in ('away', 'home')):
            raise ValueError('Each game needs away and home team identifiers.')
        for key in ('line_away','total','odds_away','odds_home','odds_over','odds_under','injury_away','injury_home','rest_margin_home','weather_total'):
            if game.get(key) is not None and (not isinstance(game[key], (int, float)) or not math.isfinite(game[key])):
                raise ValueError(f'Invalid game value: {key}')
    return data


def quotes_for(game):
    if 'quotes' in game:
        return game['quotes']
    quotes = []
    for kind, selection, field in [('spread', 'away', 'line_away'), ('spread', 'home', 'line_away'), ('total', 'over', 'total'), ('total', 'under', 'total')]:
        line, odds = game.get(field), game.get('odds_' + selection)
        if line is None or odds is None:
            continue
        quotes.append(dict(kind=kind, selection=selection, line=-line if selection == 'home' else line,
                           odds=odds, book=game.get('book', 'Imported'), updated_at=game.get('odds_updated_at')))
    return quotes


def probabilities(mean, sigma, threshold, greater):
    if sigma <= 0 or not math.isfinite(threshold) or not math.isclose(2 * threshold, round(2 * threshold)):
        raise ValueError('Use positive sigma and integer/half-point market lines.')
    if math.isclose(threshold, round(threshold), abs_tol=1e-10):
        lo = NORMAL.cdf((threshold - .5 - mean) / sigma)
        hi = NORMAL.cdf((threshold + .5 - mean) / sigma)
        return (1 - hi, hi - lo, lo) if greater else (lo, hi - lo, 1 - hi)
    below = NORMAL.cdf((threshold - mean) / sigma)
    return (1 - below, 0, below) if greater else (below, 0, 1 - below)


def evaluate(quote, result, par):
    q = copy.deepcopy(quote)
    odds, line = float(q['odds']), float(q['line'])
    if not math.isfinite(odds) or abs(odds) < 100:
        raise ValueError('American odds require absolute value at least 100.')
    selection, kind = q['selection'], q['kind']
    if kind == 'spread' and selection in ('away', 'home'):
        mean = result['margin_away'] if selection == 'away' else -result['margin_away']
        threshold, greater, edge, sigma = -line, True, mean + line, par['sigma_spread']
        label = result['game'][selection] + f' {line:+g}'
    elif kind == 'total' and selection in ('over', 'under'):
        mean, threshold, greater, sigma = result['fair_total'], line, selection == 'over', par['sigma_total']
        edge = (mean - line) * (1 if greater else -1)
        label = selection.title() + f' {line:g}'
    else:
        raise ValueError('Unsupported market or selection.')
    win, push, loss = probabilities(mean, sigma, threshold, greater)
    profit = 100 / abs(odds) if odds < 0 else odds / 100
    q.update(label=label, edge=edge, p_win=win, p_push=push, p_loss=loss,
             profit_multiple=profit, conditional_ev=profit * win - loss)
    return q


def project(data, game, parameters=None):
    params = data['parameters'] if parameters is None else parameters
    away, home = data['teams'].get(game['away']), data['teams'].get(game['home'])
    missing = [game[side] for side, team in [('away', away), ('home', home)]
               if not team or any(team.get(k) is None for k in METRICS)]
    if missing:
        return dict(game=copy.deepcopy(game), league=game.get('league') or (away or home or {}).get('league'),
                    status='Missing statistics', missing=missing, markets=[], best_market=None)
    league = away['league']
    if home['league'] != league:
        raise ValueError('Teams must belong to the same league.')
    par = params[league]
    pace = (away['p'] + home['p']) / (2 * par['n'])
    venue = 0 if game.get('neutral', False) else par['h']
    rest = game.get('rest_margin_home', 0)
    turn = params['turnover_point_value'] * params['turnover_retention'] * (away['t'] - home['t']) / 4
    output = dict(game=copy.deepcopy(game), league=league, pace=pace, status='Projected')
    for side, own, opp, sign in [('away', away, home, -1), ('home', home, away, 1)]:
        matched = {k: (own[k] + opp['d' + k]) / 2 for k in ('y', 's', 'x')}
        components = dict(ypp=params['by'] * (matched['y'] - par['y']),
                          success=params['bs'] * (matched['s'] - par['s']),
                          explosiveness=par['bx'] * (matched['x'] - par['x']))
        base = par['q'] + params['shrink'] * sum(components.values())
        parts = dict(pace_base=pace * base, turnover=-sign * turn,
                     venue=sign * venue / 2, rest=sign * rest / 2,
                     injury=game.get('injury_' + side, 0), weather=game.get('weather_total', 0) / 2)
        score = sum(parts.values())
        if not math.isfinite(score) or score <= 0:
            raise ValueError('Inputs produce a nonpositive score. Revise the assumptions.')
        output[side] = dict(matched=matched, feature_contributions=components,
                            base=base, score_parts=parts, score=score)
    margin = output['away']['score'] - output['home']['score']
    total = output['away']['score'] + output['home']['score']
    output.update(margin_away=margin, fair_line_away=-margin, fair_total=total)
    output['spread_edge_away'] = margin + game['line_away'] if game.get('line_away') is not None else None
    output['total_edge_over'] = total - game['total'] if game.get('total') is not None else None
    output['markets'], output['quote_errors'] = [], []
    for quote in quotes_for(game):
        try:
            output['markets'].append(evaluate(quote, output, par))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            output['quote_errors'].append(str(exc))
    output['best_market'] = max(output['markets'], key=lambda x: x['conditional_ev'], default=None)
    if not output['markets']:
        output['status'] = 'Projection only — no usable odds'
    return output


def run(data, parameters=None):
    rows = []
    for game in data['games']:
        try:
            rows.append(project(data, game, parameters))
        except (ValueError, ArithmeticError, TypeError, KeyError, ZeroDivisionError) as exc:
            rows.append(dict(game=copy.deepcopy(game), league=game.get('league'), status=str(exc), markets=[], best_market=None))
    return rows
