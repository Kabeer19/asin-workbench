from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from engine import (ROOT, INPUTS, CALCULATIONS, Settings, InputError, calculate,
                    read_table, csv_bytes, excel_bytes, compare_prices, load_rules)
from single_checker import render_single_checker

st.set_page_config(page_title='ASIN Workbench', page_icon='📦', layout='wide')
st.markdown('''<style>
.block-container {max-width:1540px;padding-top:2rem;padding-bottom:3rem;}
[data-testid="stSidebar"] {border-right:1px solid #E4EAF3;}
.eyebrow {font-size:12px;font-weight:700;letter-spacing:2px;color:#53719B;}
.intro {font-size:17px;color:#5D6D84;max-width:850px;margin-bottom:25px;}
[data-testid="stMetric"] {background:white;border:1px solid #E4EAF3;border-radius:12px;padding:16px;}
[data-testid="stMetricLabel"] {color:#5D6D84;}
div[data-testid="stFileUploader"] {border-radius:10px;}
h1 {letter-spacing:-1.3px;}
</style>''', unsafe_allow_html=True)


@st.cache_data(show_spinner=False, max_entries=10)
def run_pipeline(files, settings_dict, referral_text, duty_text):
    frames = [read_table(content, name, research=(i == 0)) for i, (name, content) in enumerate(files)]
    return calculate(*frames, Settings(**settings_dict),
                     rules=(json.loads(referral_text), json.loads(duty_text)))


@st.cache_data(show_spinner=False, max_entries=12)
def export_excel(frame, metadata):
    return excel_bytes(frame, metadata)


def download_pair(frame, prefix, metadata):
    left, right = st.columns(2)
    left.download_button('Download Excel', export_excel(frame, metadata), f'{prefix}.xlsx',
                         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         key=f'excel_{prefix}', width='stretch')
    right.download_button('Download CSV', csv_bytes(frame), f'{prefix}.csv', 'text/csv',
                          key=f'csv_{prefix}', width='stretch')


referral, duty = load_rules()
with st.sidebar:
    st.markdown('### ASIN Workbench')
    st.caption('USA sourcing · Australia selling')
    st.divider()
    st.markdown('**Calculation settings**')
    fx = st.number_input('USD → AUD exchange rate', min_value=0.0001, value=1.44, step=0.01, format='%.4f')
    logistics = st.number_input('Logistics · AUD per kg', min_value=0.01, value=10.0, step=0.5)
    with st.expander('Fulfilment adjustments'):
        fba_adj = st.number_input('FBA · AUD', value=-1.0, step=0.5)
        fbm_adj = st.number_input('FBM · AUD', value=3.0, step=0.5)
        amazon_adj = st.number_input('Amazon · AUD', value=-1.0, step=0.5)
    st.divider()
    st.markdown('**Saved fee lookups**')
    st.caption(f'{len(referral["category_mappings"])} category mappings · {len(duty["subcategories"]):,} duty sub-categories')
    st.caption('Duty uses the first matching sub-category in Sheet1. Referral rules come from your supplied workbook.')
    st.caption('Reports are processed on the server hosting this app. Uploads and selections belong to your current browser session. No Amazon API calls are made.')

settings = Settings(fx, logistics, fba_adj, fbm_adj, amazon_adj)
st.markdown('<div class="eyebrow">RESEARCH TO REVIEW</div>', unsafe_allow_html=True)
st.title('Find your next ASIN.')
st.markdown('<div class="intro">Review a research list with your reports, or check one ASIN manually. Refresh prices, calculate costs, and review your Australia selling opportunities in one place.</div>', unsafe_allow_html=True)

uploads_tab, single_tab, template_tab, rules_tab = st.tabs(['Workspace', 'Single ASIN checker', 'Download template', 'Calculation rules'])

# Render this independent workflow before the bulk uploader's early stop.
with single_tab:
    render_single_checker(settings, referral, duty)

with template_tab:
    st.subheader('Start with three fields')
    st.write('Fill **ASIN**, **Sub-category**, and **Category**. Leave the remaining columns empty; the app fills them from your reports and calculates the results.')
    st.caption('Sub-category is Product type from your original sheet. Category is Updated Category. Columns are reordered to put these three inputs first; use field names rather than the old Excel letters.')
    blank = pd.DataFrame(columns=INPUTS + CALCULATIONS)
    a, b = st.columns(2)
    template_path = ROOT / 'research_template.xlsx'
    if template_path.exists():
        a.download_button('Download Excel template', template_path.read_bytes(), 'asin_research_template.xlsx',
                          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', width='stretch')
    b.download_button('Download CSV template', csv_bytes(blank), 'asin_research_template.csv', 'text/csv', width='stretch')
    st.dataframe(pd.DataFrame([{'ASIN':'B09CKJM7VP','Sub-category':'PET_SUPPLIES','Category':'Pet Products'}]), hide_index=True, width='stretch')
    st.caption('Example only. Use the actual sub-category and category supplied by your research team.')
    with st.expander('Supported categories'):
        st.dataframe(pd.DataFrame([{'Category':k, 'Amazon category':v['amazon_category'], 'Fee criteria':v['source_rule']}
                                   for k,v in referral['category_mappings'].items()]), hide_index=True, width='stretch')
    with st.expander('Supported duty sub-categories'):
        st.dataframe(pd.DataFrame([{'Sub-category':v['label'], 'Duty %':v['duty_percent'],
                                    'First source row':v['first_source_row']}
                                   for v in duty['subcategories'].values()]), hide_index=True, width='stretch')

with rules_tab:
    st.subheader('Your rules, applied consistently')
    st.table(pd.DataFrame([
        ['US purchase price', 'First ASIN match → BuyBoxPrices. No offer filters.'],
        ['AU selling price', 'Filter buybox_winner=True, then first ASIN match → BuyBoxPrices.'],
        ['Fulfilment', 'From the same AU row: Amazon seller ANEGB3WVEVKZB + fulfilled=True → Amazon; other fulfilled=True → FBA; False → FBM.'],
        ['Applied selling price', 'AU selling price + fulfilment adjustment.'],
        ['Referral fee', 'Category → full fee criteria; apply whole-price thresholds or marginal tiers to the adjusted selling price.'],
        ['Closing / FBA fees', 'First ASIN match → VariableClosingFee / FBAfees. Ignore ReferralFee and total_fees.'],
        ['Weight / rank', 'First ASIN match → Package Weight (g); take the leftmost Sales Rank value.'],
        ['Duty', 'Product type / Sub-category → first Sheet1 match; purchase AUD × duty % ÷ 100.'],
        ['GST', 'Adjusted selling price × 10 / 110.'],
        ['Total cost', 'Purchase AUD + referral + closing + FBA + duty + GST + logistics.'],
        ['Profit / loss', 'Adjusted selling price − total cost.'],
        ['Missing data', 'Show a row error. Never replace a missing input with zero or an older price.'],
    ], columns=['Field', 'Rule']).set_index('Field'))
    st.caption('All monetary calculations use full precision; amounts are displayed with two decimals. Downloaded results are calculated snapshots. Refresh reports in the app to recalculate.')
    st.caption('Referral reference date in your workbook: 13 August 2026. Mapped category notes are retained. Rates have not been independently checked against the live Amazon website.')
    c1,c2 = st.columns(2)
    c1.download_button('Download referral rules JSON', json.dumps(referral, indent=2), 'referral_fees.json', 'application/json')
    c2.download_button('Download duty lookup JSON', json.dumps(duty, indent=2), 'duty_rates.json', 'application/json')

with uploads_tab:
    st.subheader('1. Add your files')
    st.caption('Five inputs per run. Replacing a file or changing a setting refreshes the results automatically. CSV and XLSX are supported.')
    specs = [
        ('Research list', 'ASIN · Sub-category · Category', 'research'),
        ('US purchase prices', 'asin · BuyBoxPrices · USD', 'us'),
        ('AU selling prices', 'Buy Box offers · fulfilment · seller', 'au'),
        ('AU fee report', 'FBAfees · VariableClosingFee', 'fees'),
        ('Weight & sales rank', 'Package Weight (g) · Sales Rank', 'details'),
    ]
    files=[]
    for start in [0,3]:
        for col,(title,caption,k) in zip(st.columns(3), specs[start:start+3]):
            with col.container(border=True):
                st.markdown(f'**{specs.index((title,caption,k))+1:02d} · {title}**')
                st.caption(caption)
                files.append(st.file_uploader(title, type=['csv','xlsx'], key=k, label_visibility='collapsed'))
    received=sum(f is not None for f in files)
    st.progress(received / 5, text=f'{received} of 5 inputs ready')
    if received < 5:
        st.info('Download the research template, fill the first three columns, then add all five files above. Your original MATCH ASIN workbook is also supported.')
        st.stop()
    file_payload=tuple((f.name, f.getvalue()) for f in files)
    try:
        with st.spinner('Matching ASINs and recalculating…'):
            results=run_pipeline(file_payload, asdict(settings), json.dumps(referral), json.dumps(duty))
    except InputError as exc:
        st.error(str(exc))
        st.stop()
    metadata={**asdict(settings), **{title: f.name for (title,_,_),f in zip(specs,files)},
              'Generated (Asia/Kolkata)':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(timespec='seconds'),
              'Match policy':'First matching source row. AU buybox_winner=True first.',
              'Referral rules source':referral.get('source_file',''), 'Duty rules source':duty.get('source_file','')}
    ready=results['Status'].eq('Ready')
    positive=ready & results['P & L (AUD)'].gt(0)
    st.divider()
    st.subheader('2. Review your results')
    a,b,c,d=st.columns(4)
    a.metric('Research rows', f'{len(results):,}')
    b.metric('Ready to review', f'{ready.sum():,}')
    c.metric('Positive P&L', f'{positive.sum():,}')
    d.metric('Rows with errors', f'{(~ready).sum():,}')
    if (~ready).any():
        st.warning('Some ASINs have missing or invalid data. Open Errors to see the exact fields. These rows cannot be selected for the final export.')
    review, changes, errors_tab=st.tabs(['Results', 'Price changes', 'Errors'])
    with review:
        search_col,status_col,method_col=st.columns([2,1,1])
        query=search_col.text_input('Search ASIN, category or brand', placeholder='Search your research list…')
        status=status_col.selectbox('View', ['All rows','Ready','Positive P&L','Zero / negative P&L','Errors'])
        method=method_col.multiselect('Fulfilment', ['Amazon','FBA','FBM'])
        with st.expander('More filters'):
            fa,fb,fc=st.columns(3)
            category=fa.multiselect('Category', sorted(results['Category'].unique()))
            min_profit=fb.number_input('Minimum P&L (AUD)', value=0.0)
            use_min=fb.checkbox('Apply minimum P&L')
            max_rank=fc.number_input('Maximum sales rank', min_value=1, value=100000, step=1000)
            use_rank=fc.checkbox('Apply maximum rank')
        view=results.copy()
        if query:
            mask=pd.Series(False,index=view.index)
            for field in ['ASIN','Category','Sub-category','Brand name']:
                mask |= view[field].astype(str).str.contains(query,case=False,regex=False)
            view=view[mask]
        if status=='Ready': view=view[view.Status.eq('Ready')]
        elif status=='Positive P&L': view=view[view.Status.eq('Ready') & view['P & L (AUD)'].gt(0)]
        elif status=='Zero / negative P&L': view=view[view.Status.eq('Ready') & view['P & L (AUD)'].le(0)]
        elif status=='Errors': view=view[view.Status.eq('Error')]
        if method: view=view[view['Fulfilment Method'].isin(method)]
        if category: view=view[view['Category'].isin(category)]
        if use_min: view=view[view['P & L (AUD)'].ge(min_profit)]
        if use_rank: view=view[view['SELLING RANK'].le(max_rank)]
        full=st.checkbox('Show all calculation columns')
        primary=['ASIN','Sub-category','Category','Fulfilment Method','PURCHASE PRICE IN USD',
                 'OUR APPLIED SELLING PRICE IN AUD','TOTAL COST','P & L (AUD)','SELLING RANK','Status','Errors']
        shown=view if full else view[primary]
        column_config={c:st.column_config.NumberColumn(format='%.2f') for c in shown.select_dtypes('number').columns if c!='SELLING RANK'}
        column_config['SELLING RANK']=st.column_config.NumberColumn(format='%d')
        column_config['Amazon AUS']=st.column_config.LinkColumn('Amazon AUS',display_text='Open product')
        st.dataframe(shown,hide_index=True,width='stretch',height=440,column_config=column_config)
        st.caption(f'{len(view):,} of {len(results):,} rows shown. Positive P&L is a filter, not an automatic final-selection decision.')
        with st.expander('Download all results', expanded=True):
            download_pair(results, 'asin_results', metadata)
        with st.expander(f'Download current view ({len(view):,} rows)'):
            download_pair(view, 'asin_filtered', metadata)
        st.markdown('#### Final selection')
        choices=results.loc[ready,'ASIN'].drop_duplicates().tolist()
        saved=st.session_state.get('selected_asins',[])
        st.session_state['selected_asins']=[a for a in saved if a in choices]
        selected=st.multiselect('Choose ASINs for your final export',choices,key='selected_asins',
                                 help='Selections stay attached to ASINs when reports refresh in this session. ASINs with errors are removed from selection.')
        if selected:
            final=results[ready & results.ASIN.isin(selected)]
            st.caption(f'{len(final)} selected rows. Input duplicates are preserved.')
            download_pair(final,'asin_selected',metadata)
    with changes:
        st.write('Upload a previous results download to compare it with the current reports.')
        st.caption('Use an ASIN Results export from this app. First previous row is used for repeated ASINs. Comparison is by ASIN, so sorting does not change the matches.')
        previous_file=st.file_uploader('Previous results (optional)',type=['csv','xlsx'],key='previous')
        if previous_file:
            try:
                previous=read_table(previous_file.getvalue(),previous_file.name)
                comparison=compare_prices(results,previous)
                changed=st.checkbox('Only show changed, new or unavailable values',value=True)
                delta_fields=[c for c in comparison if c.endswith('— change')]
                if changed:
                    mask=comparison[delta_fields].abs().gt(0.000001).any(axis=1) | comparison['Comparison'].eq('New ASIN')
                    mask |= comparison[delta_fields].isna().any(axis=1)
                    comparison=comparison[mask]
                st.dataframe(comparison,hide_index=True,width='stretch')
                download_pair(comparison,'asin_price_changes',metadata)
            except InputError as exc:
                st.error(str(exc))
        else:
            st.info('Download today’s results and upload that export here after your next report refresh.')
    with errors_tab:
        invalid=results[~ready][['ASIN','Sub-category','Category','Errors','Notes']]
        if invalid.empty:
            st.success('Every research row has the required data.')
        else:
            st.dataframe(invalid,hide_index=True,width='stretch')
            st.download_button('Download error list',csv_bytes(invalid),'asin_errors.csv','text/csv')
