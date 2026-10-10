"""Explicit-refresh NFL/CFB data adapters. No embedded API keys or guessed inputs."""
from __future__ import annotations
import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd
from model import METRICS, validate

ROOT = Path(__file__).resolve().parent
CFBD = 'https://api.collegefootballdata.com'
NFL_SCHEDULE = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
NFL_PBP = 'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz'
NFL_NAMES = dict(ARI='Arizona Cardinals', ATL='Atlanta Falcons', BAL='Baltimore Ravens', BUF='Buffalo Bills', CAR='Carolina Panthers', CHI='Chicago Bears', CIN='Cincinnati Bengals', CLE='Cleveland Browns', DAL='Dallas Cowboys', DEN='Denver Broncos', DET='Detroit Lions', GB='Green Bay Packers', HOU='Houston Texans', IND='Indianapolis Colts', JAX='Jacksonville Jaguars', KC='Kansas City Chiefs', LA='Los Angeles Rams', LAC='Los Angeles Chargers', LV='Las Vegas Raiders', MIA='Miami Dolphins', MIN='Minnesota Vikings', NE='New England Patriots', NO='New Orleans Saints', NYG='New York Giants', NYJ='New York Jets', PHI='Philadelphia Eagles', PIT='Pittsburgh Steelers', SEA='Seattle Seahawks', SF='San Francisco 49ers', TB='Tampa Bay Buccaneers', TEN='Tennessee Titans', WAS='Washington Commanders')


def normal_name(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def field(row, camel, snake=None, default=None):
    return row.get(camel, row.get(snake or camel, default))


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def request_bytes(url, params=None, headers=None, ttl=1800):
    query = '?' + urlencode(params) if params else ''
    target = url + query
    fingerprint = hashlib.sha256((target + (headers or {}).get('Authorization', '')).encode()).hexdigest()
    cache = ROOT / '.cache' / (fingerprint + '.bin')
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
        return cache.read_bytes()
    req = Request(target, headers={'User-Agent': 'FootballModelLab/1.0 academic research', **(headers or {})})
    try:
        with urlopen(req, timeout=45) as response:
            content = response.read()
    except HTTPError as exc:
        raise RuntimeError(f'{urlparse(url).hostname}: HTTP {exc.code}. Check the API key, endpoint access, and quota.') from None
    except (URLError, TimeoutError):
        raise RuntimeError(f'{urlparse(url).hostname}: network request failed.') from None
    cache.parent.mkdir(exist_ok=True)
    temp = cache.with_suffix('.tmp')
    temp.write_bytes(content)
    temp.replace(cache)
    return content


def request_json(url, params=None, headers=None, ttl=1800):
    try:
        value = json.loads(request_bytes(url, params, headers, ttl))
    except json.JSONDecodeError:
        raise RuntimeError(f'{urlparse(url).hostname}: response was not valid JSON.') from None
    if not isinstance(value, list):
        raise RuntimeError(f'{urlparse(url).hostname}: expected a list response. Check API access and schema.')
    return value


def empty_team(league, name):
    return dict(league=league, name=name, **{k: None for k in METRICS})


def game_defaults(league, away, home, game_id, kickoff):
    return dict(league=league, away=away, home=home, id=str(game_id), kickoff=kickoff,
                quotes=[], line_away=None, total=None, consensus_line_away=None, consensus_total=None,
                neutral=False, rest_margin_home=0, injury_away=0, injury_home=0, weather_total=0,
                modifiers_verified=False, notes='Injury, weather, travel and extra-rest modifiers are unverified; defaults are zero.')


def nfl_features(pbp, through_week):
    """Eligible scrimmage plays; completed prior weeks only, not the forecast week."""
    df = pbp[(pbp['season_type'] == 'REG') & (pd.to_numeric(pbp['week']) <= through_week)].copy()
    if df.empty:
        return {}
    eligible = df['play_type'].isin(['run', 'pass']) & df['posteam'].notna() & df['defteam'].notna()
    for flag in ('no_play', 'qb_kneel', 'qb_spike'):
        if flag in df:
            eligible &= pd.to_numeric(df[flag], errors='coerce').fillna(0) == 0
    plays = df[eligible].copy()
    plays['yards_gained'] = pd.to_numeric(plays['yards_gained'], errors='coerce')
    plays['epa'] = pd.to_numeric(plays['epa'], errors='coerce')
    games = defaultdict(set)
    turnover_net = defaultdict(float)
    for _, row in df.iterrows():
        a, h = row.get('posteam'), row.get('defteam')
        if pd.notna(a): games[a].add(row['game_id'])
        if pd.notna(h): games[h].add(row['game_id'])
        if row.get('interception') == 1 and pd.notna(a) and pd.notna(h):
            turnover_net[a] -= 1; turnover_net[h] += 1
        # Attribute each recoverable fumble to its actual fumbling/recovering teams.
        for i in (1, 2):
            fumbling, recovering = row.get(f'fumbled_{i}_team'), row.get(f'fumble_recovery_{i}_team')
            if pd.notna(fumbling) and pd.notna(recovering) and fumbling != recovering:
                turnover_net[fumbling] -= 1; turnover_net[recovering] += 1
    features = {}
    for code in sorted(set(plays['posteam'].dropna()) | set(plays['defteam'].dropna())):
        off, defense = plays[plays['posteam'] == code], plays[plays['defteam'] == code]
        if off.empty or defense.empty: continue
        off_pass, def_pass = off[off['pass_attempt'] == 1], defense[defense['pass_attempt'] == 1]
        if off_pass.empty or def_pass.empty or not games[code]: continue
        if off[['yards_gained', 'epa']].isna().any().any() or defense[['yards_gained', 'epa']].isna().any().any():
            continue
        team = dict(league='NFL', name=NFL_NAMES.get(code, code), y=float(off['yards_gained'].mean()),
                    dy=float(defense['yards_gained'].mean()), s=float((off['epa'] > 0).mean() * 100),
                    ds=float((defense['epa'] > 0).mean() * 100), x=float((off_pass['yards_gained'] >= 20).mean() * 100),
                    dx=float((def_pass['yards_gained'] >= 20).mean() * 100), p=len(off) / len(games[code]),
                    t=turnover_net[code] / len(games[code]), sample_games=len(games[code]), sample_plays=len(off),
                    feature_through_week=through_week, source='nflverse prior-week play-by-play')
        features['NFL:' + code] = team
    return features


def load_nfl(season, start, end, fetch=request_bytes):
    schedule = pd.read_csv(io.BytesIO(fetch(NFL_SCHEDULE)))
    days = pd.to_datetime(schedule['gameday']).dt.date
    chosen = schedule[(schedule['season'] == season) & (schedule['game_type'] == 'REG') & (days >= start) & (days <= end)]
    # The screen intentionally includes already completed games in the selected window;
    # their predictions still use only prior weeks. Current odds may be unavailable.
    if chosen.empty: return {}, [], {}, []
    cutoff = int(chosen['week'].min()) - 1
    columns = {'game_id','season_type','week','posteam','defteam','play_type','yards_gained','epa','pass_attempt','interception','no_play','qb_kneel','qb_spike','fumbled_1_team','fumbled_2_team','fumble_recovery_1_team','fumble_recovery_2_team'}
    warnings = []
    try:
        pbp = pd.read_csv(io.BytesIO(fetch(NFL_PBP.format(season=season), ttl=3600)), compression='gzip', usecols=lambda c:c in columns, low_memory=False)
        required = {'game_id','season_type','week','posteam','defteam','play_type','yards_gained','epa','pass_attempt','interception','fumbled_1_team','fumble_recovery_1_team'}
        if required - set(pbp.columns): raise RuntimeError('nflverse play-by-play is missing required columns.')
        teams = nfl_features(pbp, cutoff)
    except (RuntimeError, ValueError) as exc:
        teams = {}; warnings.append('NFL statistics unavailable: ' + str(exc))
    games, aliases = [], {}
    for _, row in chosen.iterrows():
        codes = [str(row['away_team']), str(row['home_team'])]
        for code in codes:
            tid='NFL:'+code; teams.setdefault(tid, empty_team('NFL', NFL_NAMES.get(code,code)))
            for alias in (code, NFL_NAMES.get(code, code)):
                aliases[normal_name(alias)] = tid
        g = game_defaults('NFL', 'NFL:'+codes[0], 'NFL:'+codes[1], row['game_id'], str(row['gameday'])+' '+str(row.get('gametime',''))+' America/New_York')
        g['week']=int(row['week']);g['feature_through_week']=cutoff
        g['neutral']=str(row.get('location','')).lower()=='neutral'
        if pd.notna(row.get('away_score')) and pd.notna(row.get('home_score')):
            g['completed']=True;g['actual_away']=float(row['away_score']);g['actual_home']=float(row['home_score'])
        games.append(g)
    return teams, games, aliases, warnings


def box_metrics(rows, allowed_ids):
    accum = defaultdict(lambda:dict(games=0, yards=0, plays=0, allowed_yards=0, allowed_plays=0, net_to=0))
    for game in rows:
        if str(game.get('id')) not in allowed_ids: continue
        entries = game.get('teams', [])
        if len(entries)!=2: continue
        parsed=[]
        for team in entries:
            stats={x.get('category'):x.get('stat') for x in team.get('stats', [])}
            attempts=number(stats.get('passingAttempts'))
            if attempts is None:
                text=stats.get('completionAttempts')
                if isinstance(text,str) and re.fullmatch(r'\d+\s*[-/]\s*\d+',text):
                    attempts=number(re.split(r'[-/]',text)[-1].strip())
            rushes, yards, turnovers = number(stats.get('rushingAttempts')),number(stats.get('totalYards')),number(stats.get('turnovers'))
            if any(x is None for x in (attempts,rushes,yards,turnovers)):
                parsed=[];break
            parsed.append((field(team,'school','team'), yards, attempts+rushes, turnovers))
        if len(parsed)!=2 or any(x[2]<=0 for x in parsed): continue
        for i,(name,yards,plays,giveaways) in enumerate(parsed):
            opponent=parsed[1-i];a=accum[name]
            a['games']+=1;a['yards']+=yards;a['plays']+=plays;a['allowed_yards']+=opponent[1];a['allowed_plays']+=opponent[2];a['net_to']+=opponent[3]-giveaways
    return accum


def load_cfb(season, start, end, api_key, fetch=request_json):
    if not api_key: raise RuntimeError('A CollegeFootballData API key is required for CFB data.')
    headers={'Authorization':'Bearer '+api_key}
    schedule=fetch(CFBD+'/games',dict(year=season,seasonType='regular',classification='fbs'),headers)
    chosen=[]
    for g in schedule:
        kickoff=field(g,'startDate','start_date')
        if not kickoff: continue
        day=datetime.fromisoformat(kickoff.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date()
        if start<=day<=end: chosen.append(g)
    if not chosen:return {},[],{},[]
    cutoff=int(min(g['week'] for g in chosen))-1
    prior=[g for g in schedule if int(g['week'])<=cutoff and field(g,'homePoints','home_points') is not None and field(g,'awayPoints','away_points') is not None]
    allowed_ids={str(g['id']) for g in prior}
    weeks=sorted({int(g['week']) for g in prior})
    warnings=[];advanced=[];boxes=[]
    try:
        if cutoff>=0:
            advanced=fetch(CFBD+'/stats/season/advanced',dict(year=season,startWeek=0,endWeek=cutoff,excludeGarbageTime='false',classification='fbs'),headers)
        for week in weeks:
            boxes.extend(fetch(CFBD+'/games/teams',dict(year=season,week=week,seasonType='regular',classification='fbs'),headers,ttl=86400))
    except RuntimeError as exc:
        warnings.append('CFB statistics incomplete: '+str(exc))
    raw=box_metrics(boxes,allowed_ids)
    expected_counts=defaultdict(int)
    for g in prior:
        for side in ('home','away'):expected_counts[field(g,side+'Team',side+'_team')]+=1
    teams={}
    for row in advanced:
        name=row['team'];box=raw.get(name);o,d=row.get('offense',{}),row.get('defense',{})
        values=[field(o,'successRate','success_rate'),field(d,'successRate','success_rate'),o.get('explosiveness'),d.get('explosiveness')]
        # Do not mix a partial box-score sample with a complete advanced-stat sample.
        if not box or box['games']!=expected_counts[name] or any(number(x) is None for x in values):continue
        teams['CFB:'+name]=dict(league='CFB',name=name,y=box['yards']/box['plays'],dy=box['allowed_yards']/box['allowed_plays'],s=100*float(values[0]),ds=100*float(values[1]),x=float(values[2]),dx=float(values[3]),p=box['plays']/box['games'],t=box['net_to']/box['games'],sample_games=box['games'],sample_plays=box['plays'],feature_through_week=cutoff,source='CFBD prior-week advanced stats and box scores')
    aliases={}
    try:
        for team in fetch(CFBD+'/teams/fbs',dict(year=season),headers,ttl=86400):
            school=team['school'];tid='CFB:'+school
            names=[school, school+' '+str(team.get('mascot','')),team.get('abbreviation')]+(field(team,'alternateNames','alternate_names',[]) or [])
            for alias in names:
                if alias:
                    key=normal_name(alias)
                    if key in aliases and aliases[key]!=tid:aliases[key]=None
                    else:aliases[key]=tid
    except RuntimeError as exc:warnings.append('CFB name aliases unavailable: '+str(exc))
    games=[]
    for row in chosen:
        a,h=field(row,'awayTeam','away_team'),field(row,'homeTeam','home_team')
        for name in (a,h):
            tid='CFB:'+name;teams.setdefault(tid,empty_team('CFB',name));aliases[normal_name(name)]=tid
        g=game_defaults('CFB','CFB:'+a,'CFB:'+h,row['id'],field(row,'startDate','start_date'))
        g['week']=row['week'];g['feature_through_week']=cutoff;g['neutral']=bool(field(row,'neutralSite','neutral_site',False))
        if field(row,'awayPoints','away_points') is not None and field(row,'homePoints','home_points') is not None:
            g['completed']=True;g['actual_away']=field(row,'awayPoints','away_points');g['actual_home']=field(row,'homePoints','home_points')
        games.append(g)
    return teams,games,aliases,warnings


def attach_odds(games, events, aliases):
    unmatched=[]
    for event in events:
        a,h=aliases.get(normal_name(event.get('away_team'))),aliases.get(normal_name(event.get('home_team')))
        candidates=[g for g in games if g['away']==a and g['home']==h]
        if len(candidates)!=1:
            unmatched.append(event.get('away_team','?')+' @ '+event.get('home_team','?'));continue
        g=candidates[0]
        # Prevent a same-opponents event from another week entering this snapshot.
        event_day=datetime.fromisoformat(event['commence_time'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date()
        kickoff=g['kickoff']
        game_day=(date.fromisoformat(kickoff[:10]) if 'America/New_York' in kickoff else datetime.fromisoformat(kickoff.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date())
        if event_day!=game_day:continue
        for book in event.get('bookmakers',[]):
            for market in book.get('markets',[]):
                kind={'spreads':'spread','totals':'total'}.get(market.get('key'))
                if not kind:continue
                for outcome in market.get('outcomes',[]):
                    label=outcome.get('name')
                    if kind=='spread':selection='away' if label==event['away_team'] else 'home' if label==event['home_team'] else None
                    else:selection={'Over':'over','Under':'under'}.get(label)
                    line,odds=number(outcome.get('point')),number(outcome.get('price'))
                    if selection and line is not None and odds is not None:
                        g['quotes'].append(dict(kind=kind,selection=selection,line=line,odds=odds,book=book.get('title',book['key']),updated_at=market.get('last_update',book.get('last_update'))))
        away_lines=[q['line'] for q in g['quotes'] if q['selection']=='away']
        over_lines=[q['line'] for q in g['quotes'] if q['selection']=='over']
        g['line_away']=median(away_lines) if away_lines else None;g['total']=median(over_lines) if over_lines else None
        g['consensus_line_away']=g['line_away'];g['consensus_total']=g['total'];g['book']='Median reference (not tradable)'
    return unmatched


def build_snapshot(season, start, end, leagues=('NFL','CFB'), cfbd_key='', odds_key='', regions='us'):
    if end<start or (end-start).days>30:raise ValueError('Choose an ordered date window of at most 31 days.')
    baseline=json.loads((ROOT/'snapshot.json').read_text())
    out=dict(mode='connected',as_of=datetime.now(timezone.utc).isoformat(timespec='seconds'),snapshot_date=date.today().isoformat(),window=f'{start} through {end} America/New_York',parameters=copy.deepcopy(baseline['parameters']),teams={},games=[],warnings=[],sources={'NFL schedule':NFL_SCHEDULE,'NFL play-by-play':NFL_PBP.format(season=season),'CFB':'https://collegefootballdata.com/','Market quotes':'https://the-odds-api.com/liveapi/guides/v4/'},consensus_method='Median quoted away handicaps and over totals are comparison summaries only. EV is evaluated on each named book quote.')
    for league in leagues:
        try:
            teams,games,aliases,warnings=load_nfl(season,start,end) if league=='NFL' else load_cfb(season,start,end,cfbd_key)
        except (RuntimeError, ValueError, KeyError) as exc:
            out['warnings'].append(league+' data unavailable: '+str(exc));continue
        out['teams'].update(teams);out['games'].extend(games);out['warnings'].extend(warnings)
        if odds_key and games:
            sport='americanfootball_nfl' if league=='NFL' else 'americanfootball_ncaaf'
            try:
                events=request_json('https://api.the-odds-api.com/v4/sports/'+sport+'/odds',dict(apiKey=odds_key,regions=regions,markets='spreads,totals',oddsFormat='american'),ttl=120)
                missed=attach_odds(games,events,aliases)
                if missed:out['warnings'].append(f'{league}: {len(missed)} odds events could not be joined uniquely; they were not guessed.')
            except (RuntimeError,ValueError,KeyError) as exc:out['warnings'].append(league+' odds unavailable: '+str(exc))
        elif games:out['warnings'].append(league+': no Odds API key. Score projections are available where statistics are complete; market EV is unavailable.')
    if not out['games']:raise RuntimeError('No schedule games were returned. '+(' '.join(out['warnings']) or 'Try another date window.'))
    validate(out)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season',type=int,required=True)
    parser.add_argument('--start',type=date.fromisoformat,required=True)
    parser.add_argument('--end',type=date.fromisoformat,required=True)
    parser.add_argument('--leagues',nargs='+',choices=['NFL','CFB'],default=['NFL','CFB'])
    parser.add_argument('--output',type=Path,default=Path('full_slate.json'))
    args=parser.parse_args()
    snapshot=build_snapshot(args.season,args.start,args.end,args.leagues,os.getenv('CFBD_API_KEY',''),os.getenv('ODDS_API_KEY',''))
    args.output.write_text(json.dumps(snapshot,indent=2,allow_nan=False)+'\n')
    print(f'Saved {len(snapshot["games"])} games to {args.output}.')
    for warning in snapshot['warnings']:print(warning)
