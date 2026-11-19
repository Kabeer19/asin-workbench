"""The no-upload, manual single-ASIN workflow."""
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from engine import InputError, calculate_single, csv_bytes, excel_bytes


def render_single_checker(settings, referral, duty):
    st.subheader('Check one ASIN')
    st.write('Enter the prices, package weight and fees below. The app applies your saved referral, duty and fulfilment rules—no file uploads needed.')
    st.caption('Manual entry only. Prices and fees are not fetched from Amazon. The sidebar exchange rate, logistics rate and adjustments apply here too.')
    category_keys = sorted(referral['category_mappings'])
    subcategory_keys = sorted(k for k in duty['subcategories'] if k not in ('', '#n/a', 'n/a', '0', 'none', 'null'))
    with st.form('single_asin_form'):
        a, b, c = st.columns(3)
        a.text_input('ASIN', key='single_asin', placeholder='e.g. B0FJL4YW73', max_chars=30)
        b.selectbox('Category', category_keys, index=None, key='single_category',
                    placeholder='Search category…', format_func=lambda k: k.upper() if k in ('pc', 'biss') else k.title())
        c.selectbox('Sub-category / Product type', subcategory_keys, index=None, key='single_subcategory',
                    placeholder='Search product type…', format_func=lambda k: duty['subcategories'][k]['label'])
        a, b, c = st.columns(3)
        a.number_input('US Buy Box price (USD)', min_value=0.0, value=None, step=0.01,
                       format='%.2f', key='single_us_price', placeholder='Enter purchase price')
        b.number_input('AU Buy Box price (AUD)', min_value=0.0, value=None, step=0.01,
                       format='%.2f', key='single_au_price', placeholder='Enter selling price')
        c.selectbox('AU fulfilment', ['Amazon', 'FBA', 'FBM'], index=None, key='single_fulfilment',
                    placeholder='Choose fulfilment')
        a, b, c = st.columns(3)
        a.number_input('Package weight (grams)', min_value=0.0, value=None, step=1.0,
                       format='%.2f', key='single_weight', placeholder='Enter package weight')
        b.number_input('FBA fee (AUD)', min_value=0.0, value=None, step=0.01,
                       format='%.2f', key='single_fba_fee', placeholder='Enter fee, or 0 if applicable')
        c.number_input('Closing fee (AUD)', min_value=0.0, value=None, step=0.01,
                       format='%.2f', key='single_closing_fee', placeholder='Enter fee, or 0 if applicable')
        with st.expander('Optional details'):
            a, b = st.columns(2)
            a.text_input('Sales rank (optional)', key='single_rank', placeholder='e.g. 5588, 55')
            b.text_input('Brand (optional)', key='single_brand')
        submitted = st.form_submit_button('Calculate profitability', type='primary', width='stretch')
    if submitted:
        st.session_state['single_calculated_inputs'] = dict(
            asin_value=st.session_state['single_asin'], category=st.session_state['single_category'],
            subcategory=st.session_state['single_subcategory'], us_price=st.session_state['single_us_price'],
            au_price=st.session_state['single_au_price'], fulfilment=st.session_state['single_fulfilment'],
            package_weight_g=st.session_state['single_weight'], fba_fee=st.session_state['single_fba_fee'],
            closing_fee=st.session_state['single_closing_fee'], sales_rank=st.session_state['single_rank'],
            brand=st.session_state['single_brand'])
    payload = st.session_state.get('single_calculated_inputs')
    if payload is None:
        st.info('Fill the required fields and click Calculate profitability. Sales rank and brand are optional.')
        return
    try:
        result = calculate_single(**payload, settings=settings, rules=(referral, duty))
    except InputError as exc:
        st.error(str(exc))
        return
    r = result.iloc[0]
    if r['Status'] == 'Error':
        st.error('Please correct the following inputs:')
        for error in r['Errors'].split('; '):
            st.write(f'• {error}')
        return
    st.divider()
    st.markdown(f'#### Profitability for {r["ASIN"]}')
    st.caption('Results use the last submitted fields and the current sidebar settings. Submit again after editing the form.')
    a, b, c, d = st.columns(4)
    a.metric('Applied selling price', f'AUD {r["OUR APPLIED SELLING PRICE IN AUD"]:,.2f}')
    b.metric('Total cost', f'AUD {r["TOTAL COST"]:,.2f}')
    c.metric('Profit / loss per unit', f'AUD {r["P & L (AUD)"]:,.2f}')
    d.metric('Profit margin', f'{r["Profit margin %"]:.2f}%')
    profit = r['P & L (AUD)']
    if profit > 0:
        st.success(f'Positive P&L of AUD {profit:,.2f} per unit under these inputs.')
    elif profit < 0:
        st.error(f'Loss of AUD {abs(profit):,.2f} per unit under these inputs.')
    else:
        st.info('This calculation breaks even.')
    st.write(f'**Referral fee criteria:** {r["REFERRAL FEE % CRITERIA"]}')
    st.write(f'**Duty:** {r["Duties %"]:g}% · **ROI on total cost:** {r["ROI % (on total cost)"]:.2f}%')
    breakdown = [
        ('US purchase converted to AUD', r['PURCHASE IN AUD']),
        ('Referral fee', r['REFERRAL FEE AUD']),
        ('Closing fee', r['CLOSING FEE AUD']),
        ('FBA fee', r['FBA FEE AUD']),
        ('Duty charges', r['Duties Charges']),
        ('GST on selling price', r['GST AUD on Selling Price']),
        ('Import logistics', r['IMPORT COST LOGISTICS']),
        ('Total cost', r['TOTAL COST']),
    ]
    st.dataframe(pd.DataFrame(breakdown, columns=['Cost', 'AUD']), hide_index=True, width='stretch',
                 column_config={'AUD': st.column_config.NumberColumn(format='%.2f')})
    st.caption(f'AU Buy Box: AUD {r["SELLING PRICE IN AUD"]:,.2f}; {r["Fulfilment Method"]} adjustment: AUD {r["Fulfilment Adjustment"]:+.2f}. Package: {r["PACKAGE WEIGHT IN GRAMS"]:g} g ({r["PACKAGE WEIGHT IN KG"]:g} kg).')
    st.caption('Profit margin = profit ÷ applied selling price. ROI = profit ÷ total cost. Both use the existing workbook cost rules.')
    if r['Notes']:
        st.caption(r['Notes'])
    metadata = {**asdict(settings), 'Input source': 'Manual single-ASIN entry',
                'Generated (Asia/Kolkata)': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(timespec='seconds')}
    a, b = st.columns(2)
    a.download_button('Download single-ASIN Excel', excel_bytes(result, metadata), f'{r["ASIN"]}_profitability.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', key='single_download_excel', width='stretch')
    b.download_button('Download single-ASIN CSV', csv_bytes(result), f'{r["ASIN"]}_profitability.csv',
                       'text/csv', key='single_download_csv', width='stretch')
