"""Run with: python -m streamlit run app.py"""
from __future__ import annotations
import copy
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from model import METRICS, project, quotes_for, run, validate
from data_pipeline import build_snapshot

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title='Football Model Lab', page_icon='🏈', layout='wide')
st.markdown('''<style>
 .stApp{background:#f5f7f2}h1,h2,h3{color:#203e31}[data-testid="stSidebar"]{background:#eaf0e4}
 [data-testid="stMetric"]{background:white;border:1px solid #dbe4d7;border-radius:10px;padding:15px}
 .block-container{padding-top:2rem}button{border-radius:7px!important}
 </style>''',unsafe_allow_html=True)


def reset_widgets():
    for key in list(st.session_state):
        if key.startswith(('weight_','scenario_','team_')) or key in ('selected_matchup','dataset_json_editor'):del st.session_state[key]


def use_dataset(next_data):
    validate(next_data)
    st.session_state.data=copy.deepcopy(next_data)
    st.session_state.baseline=copy.deepcopy(next_data)
    reset_widgets()


def secret(name):
    try:return st.secrets.get(name,os.getenv(name,''))
    except Exception:return os.getenv(name,'')


if 'data' not in st.session_state:
    use_dataset(json.loads((ROOT/'snapshot.json').read_text()))
data=st.session_state.data

with st.sidebar:
    st.title('Football Model Lab')
    st.caption('NFL + CFB · Academic research')
    st.subheader('Model controls')
    if st.button('Reset model parameters',use_container_width=True,key='reset_parameters'):
        data['parameters']=copy.deepcopy(st.session_state.baseline['parameters']);reset_widgets();st.rerun()
    p=data['parameters']
    for key,label,lo,hi,step in [('by','Yards per play weight',0.,12.,.1),('bs','Success rate weight',0.,1.5,.01),('shrink','Efficiency shrinkage',0.,1.,.01),('turnover_retention','Turnover retention',0.,1.,.01),('turnover_point_value','Turnover point value',0.,8.,.1)]:
        # Expand the widget range to preserve imported coefficients.
        value=float(p[key]);p[key]=st.slider(label,min(lo,value),max(hi,value),value,step=step,key='weight_'+key)
    selected_league=st.selectbox('League assumptions',['NFL','CFB'])
    lp=p[selected_league]
    with st.expander('Scoring, venue and uncertainty',expanded=True):
        for key,label,minimum in [('q','Baseline points per team',1.),('bx','Explosiveness weight',None),('h','Home-field margin',0.),('sigma_spread','Spread residual sigma',.1),('sigma_total','Total residual sigma',.1)]:
            kwargs=dict(value=float(lp[key]),step=.1,key='weight_'+selected_league+'_'+key)
            if minimum is not None:kwargs['min_value']=minimum
            lp[key]=st.number_input(label,**kwargs)
    with st.expander('Reference metrics'):
        for key,label,minimum in [('n','Reference plays per team',1.),('y','Reference yards per play',0.),('s','Reference success %',0.),('x','Reference explosiveness',0.)]:
            lp[key]=st.number_input(label,min_value=minimum,value=float(lp[key]),step=.1,key='weight_'+selected_league+'_'+key)
    st.caption('All coefficients and residual sigmas start as uncalibrated priors. Changing them tests assumptions.')

st.caption('RESEARCH WORKSPACE / SLATE EXPLORER')
st.title('Explore the slate.')
st.write('Rank the model estimates, inspect every game, and change the assumptions.')
mode=data.get('mode','snapshot')
st.info(('Frozen 11-game snapshot. Use Data & setup to load a full slate.' if mode=='snapshot' else 'Imported data. Verify timestamps, definitions, and coverage before interpreting the rankings.')+' Conditional EV has not been validated.')
st.caption('As of '+str(data.get('as_of',data.get('snapshot_date','unspecified')))+' · '+data.get('window',''))
for warning in data.get('warnings',[]):st.warning(warning)

tabs=st.tabs(['Slate explorer','Data & setup','Method & validation'])
results=run(data)

with tabs[0]:
    counts=st.columns(4)
    counts[0].metric('Games loaded',len(results));counts[1].metric('With projections',sum('away' in r for r in results));counts[2].metric('With usable odds',sum(bool(r['markets']) for r in results));counts[3].metric('Model version','Prototype 01')
    f=st.columns([1,1,2])
    league=f[0].selectbox('League',['All','NFL','CFB'])
    market=f[1].selectbox('Market',['All','Spreads','Totals'])
    search=f[2].text_input('Search team or matchup',placeholder='Buffalo, UCLA, Houston…')
    kind={'All':None,'Spreads':'spread','Totals':'total'}[market]
    def best(r):return max((q for q in r['markets'] if kind is None or q['kind']==kind),key=lambda q:q['conditional_ev'],default=None)
    def names(g):return data['teams'].get(g['away'],{}).get('name',g['away'])+' @ '+data['teams'].get(g['home'],{}).get('name',g['home'])
    visible=[r for r in results if (league=='All' or r['league']==league) and (not search or search.lower() in (names(r['game'])+' '+r['game']['away']+' '+r['game']['home']).lower())]
    st.subheader('Largest model discrepancies')
    st.caption('Top three per league by conditional EV; one quoted market per game.')
    for lg in (['NFL','CFB'] if league=='All' else [league]):
        ranked=sorted([r for r in visible if r['league']==lg and best(r)],key=lambda r:best(r)['conditional_ev'],reverse=True)[:3]
        st.markdown('**'+lg+'**')
        if not ranked:st.caption('No games with usable odds match this filter.')
        for col,r in zip(st.columns(3),ranked):
            q=best(r)
            with col:
                with st.container(border=True):
                    st.markdown('**'+names(r['game'])+'**')
                    st.metric('Projected score · Away – Home',f"{r['away']['score']:.1f} – {r['home']['score']:.1f}")
                    st.write(f"{q['label']} · {q['odds']:+g} · {q['book']}")
                    st.caption(f"{q['edge']:+.2f} points discrepancy · conditional EV {100*q['conditional_ev']:+.1f}%")
    st.subheader('Every loaded game')
    table=[]
    for r in visible:
        g=r['game'];q=best(r);row={'Matchup':names(g),'League':r['league'],'Status':r['status']}
        if 'away' in r:row.update({'Away score':r['away']['score'],'Home score':r['home']['score'],'Reference away spread':g.get('line_away'),'Model away spread':r['fair_line_away'],'Away spread difference':r['spread_edge_away'],'Reference total':g.get('total'),'Model total':r['fair_total'],'Over difference':r['total_edge_over']})
        row.update({'Best quoted market':q['label'] if q else None,'Book':q['book'] if q else None,'Conditional EV %':100*q['conditional_ev'] if q else None})
        table.append(row)
    df=pd.DataFrame(table)
    if not df.empty:
        if 'Conditional EV %' in df:df=df.sort_values('Conditional EV %',ascending=False,na_position='last')
        st.dataframe(df,width='stretch',hide_index=True,height=min(650,38*len(df)+40))
        st.download_button('Download displayed projections',df.to_csv(index=False),file_name='projections.csv',mime='text/csv')
    else:st.caption('No games match your filters.')
    st.caption('Reference lines may be multi-book medians. Conditional EV always uses the individual quoted line and price.')
    if visible:
        game_ids=[results.index(r) for r in visible]
        index=st.selectbox('Inspect a matchup',game_ids,format_func=lambda i:names(results[i]['game']),key='selected_matchup')
        r=results[index];g=data['games'][index]
        with st.expander('Projection breakdown',expanded=True):
            if 'away' in r:
                breakdown=pd.DataFrame({g['away']:r['away']['score_parts'],g['home']:r['home']['score_parts']})
                st.dataframe(breakdown,width='stretch')
                st.write('Matched efficiency inputs')
                st.dataframe(pd.DataFrame({g['away']:r['away']['matched'],g['home']:r['home']['matched']}),width='stretch')
                st.write(f"Pace factor = {r['pace']:.6f}. Fair away spread = {r['fair_line_away']:+.6f}. Fair total = {r['fair_total']:.6f}.")
                for side in ('away','home'):
                    m=r[side]['matched'];lg=data['parameters'][r['league']]
                    st.code(f"{g[side]}: B = {lg['q']} + {p['shrink']} × [{p['by']}×({m['y']:.4f}−{lg['y']}) + {p['bs']}×({m['s']:.4f}−{lg['s']}) + {lg['bx']}×({m['x']:.4f}−{lg['x']})] = {r[side]['base']:.6f}\nScore = "+' '.join(f'{v:+.6f}' for v in r[side]['score_parts'].values())+f" = {r[side]['score']:.6f}")
                st.dataframe(pd.DataFrame(r['markets']),width='stretch',hide_index=True)
            else:st.warning(r['status']+' '+', '.join(r.get('missing',[])))
            st.caption(g.get('notes',''))
            if g.get('modifiers_verified') is False:st.warning('Injury, weather and rest modifiers have not been verified for this pull. Defaults are zero.')
        with st.expander('Edit matchup assumptions'):
            with st.form('scenario_form_'+str(index)):
                cols=st.columns(3);values={}
                defaults={'line_away':0.,'total':45.,'odds_away':-110.,'odds_home':-110.,'odds_over':-110.,'odds_under':-110.,'injury_away':0.,'injury_home':0.,'rest_margin_home':0.,'weather_total':0.}
                labels={'line_away':'Away spread','total':'Total','odds_away':'Away odds','odds_home':'Home odds','odds_over':'Over odds','odds_under':'Under odds','injury_away':'Away injury points','injury_home':'Home injury points','rest_margin_home':'Rest margin to home','weather_total':'Weather total adjustment'}
                qs=quotes_for(g)
                for i,(key,fallback) in enumerate(defaults.items()):
                    value=g.get(key)
                    if value is None and key.startswith('odds_'):value=next((q['odds'] for q in qs if q['selection']==key[5:]),fallback)
                    value=float(fallback if value is None else value)
                    step=1. if key.startswith('odds_') else .5 if key in ('line_away','total') else .1
                    values[key]=cols[i%3].number_input(labels[key],value=value,step=step,key='scenario_'+str(index)+'_'+key)
                values['neutral']=st.checkbox('Neutral venue',value=g.get('neutral',False),key='scenario_'+str(index)+'_neutral')
                if st.form_submit_button('Apply scenario'):
                    modified={**g,**values}
                    quote_changed=any(values[k]!=(g.get(k) if g.get(k) is not None else next((q['odds'] for q in qs if q['selection']==k[5:]),defaults[k]) if k.startswith('odds_') else defaults[k]) for k in ('line_away','total','odds_away','odds_home','odds_over','odds_under'))
                    if quote_changed or not qs:
                        modified.pop('quotes',None);modified['book']='Manual scenario';modified.pop('odds_updated_at',None)
                    try:
                        project(data,modified)
                        data['games'][index]=modified;st.rerun()
                    except (ValueError,ArithmeticError) as exc:st.error(str(exc))
        with st.expander('Edit team metrics'):
            available=[tid for tid in (g['away'],g['home']) if tid in data['teams']]
            if available:
                team_id=st.selectbox('Team',available,key='team_id')
                with st.form('team_form_'+team_id):
                    current=data['teams'][team_id];inputs={};cols=st.columns(4)
                    for i,key in enumerate(METRICS):inputs[key]=cols[i%4].number_input(key,value=float(current.get(key) or 0),step=.1,key='team_'+team_id+'_'+key)
                    st.caption('y/dy: offense/allowed YPP; s/ds: success %; x/dx: explosive metric; p: plays per game; t: net turnovers per game. Zero fields above require your actual input before applying.')
                    if st.form_submit_button('Apply team statistics'):
                        data['teams'][team_id].update(inputs);st.rerun()

with tabs[1]:
    st.subheader('Load a full slate')
    st.write('Select a date window, supply provider keys, then refresh explicitly. Model-control changes reuse the loaded data.')
    today=datetime.now(ZoneInfo('America/New_York')).date()
    with st.form('data_refresh'):
        c=st.columns(3)
        season=c[0].number_input('Season',min_value=1999,max_value=2100,value=today.year,step=1)
        start=c[1].date_input('First date',today)
        end=c[2].date_input('Last date',today+timedelta(days=7))
        leagues=st.multiselect('Leagues',['NFL','CFB'],default=['NFL','CFB'])
        c=st.columns(2)
        cfbd_key=c[0].text_input('CollegeFootballData API key',value=secret('CFBD_API_KEY'),type='password')
        odds_key=c[1].text_input('The Odds API key',value=secret('ODDS_API_KEY'),type='password')
        regions=st.selectbox('Book region',['us','us2','uk','eu','au'])
        refresh=st.form_submit_button('Refresh schedules, statistics and odds')
    if refresh:
        with st.spinner('Loading schedules and prior-week statistics…'):
            try:
                current_params=copy.deepcopy(data['parameters'])
                next_data=build_snapshot(int(season),start,end,leagues,cfbd_key,odds_key,regions)
                next_data['parameters']=current_params;use_dataset(next_data);st.rerun()
            except Exception as exc:st.error('Refresh did not replace your dataset. '+str(exc))
    st.caption('NFL statistics use eligible prior-week plays. CFB uses prior-week advanced stats plus box scores. Feed access, quota and publication timing determine coverage. First-week teams need preseason priors or manually supplied statistics.')
    st.subheader('Import or export a snapshot')
    upload=st.file_uploader('Import JSON',type=['json'])
    if upload and st.button('Load uploaded snapshot'):
        try:
            parsed=json.load(upload);use_dataset(parsed.get('snapshot',parsed));st.rerun()
        except (ValueError,KeyError,TypeError) as exc:st.error(str(exc))
    st.download_button('Export model inputs and settings',json.dumps(data,indent=2,allow_nan=False),file_name='football_model_snapshot.json',mime='application/json')
    st.caption('Import the exported JSON into the private web dashboard to use the same data and coefficients there.')
    with st.expander('Edit the complete dataset JSON'):
        text=st.text_area('Teams, games, markets, parameters and sources',json.dumps(data,indent=2),height=350,key='dataset_json_editor')
        if st.button('Apply complete JSON'):
            try:use_dataset(json.loads(text));st.rerun()
            except (ValueError,KeyError,TypeError) as exc:st.error(str(exc))
    if st.button('Restore original 11-game snapshot'):
        use_dataset(json.loads((ROOT/'snapshot.json').read_text()));st.rerun()

with tabs[2]:
    st.subheader('Score equations')
    st.code('M = (offensive metric + opponent allowed metric) / 2\nF = (away plays + home plays) / (2 × reference plays)\nB = baseline + shrink × [bY×(Y−Yref) + bS×(S−Sref) + bX×(X−Xref)]\nT = turnover point value × retention × (away net TO − home net TO) / 4\nAway score = F×Baway + T − (home field+rest)/2 + injury away + weather/2\nHome score = F×Bhome − T + (home field+rest)/2 + injury home + weather/2\nFair away spread = home score − away score\nFair total = home score + away score\nConditional EV = net profit multiple × P(win) − P(loss)')
    st.write('Home field is zero at neutral venues. Extra travel is currently absorbed into home field. Turnover retention is a relative-rating proxy; it is not a separately estimated fumble recovery model.')
    st.write('Normal residuals use integer push bins and half-point lines. The distribution does not model football scoring clusters at 3 and 7. Uncertainty and probabilities need out-of-sample calibration.')
    st.subheader('The path to prediction quality')
    st.markdown('''1. Store immutable, timestamped features and odds before kickoff, plus eventual results.
2. Fit separate NFL and CFB models with opponent adjustment, regularization and sample-size shrinkage.
3. Validate on later weeks/seasons. Compare margin and total MAE/RMSE with a timestamp-matched market baseline.
4. Calibrate cover/over probabilities and the score distribution on held-out games. Evaluate Brier score, log loss and calibration plots.
5. Select weights and thresholds on training/validation data; report final test results once. Rank future games only after measuring uncertainty and stability.
''')
    st.write('This package supplies an editable prototype and data pipeline. It does not contain historically fitted coefficients, an automated injury feed, or proof of profitable returns.')
    st.json(data.get('sources',{}))
