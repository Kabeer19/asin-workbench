"""Deterministic ASIN joins and the agreed AU profitability calculations."""
from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
INPUTS = ['ASIN', 'Sub-category', 'Category']
CALCULATIONS = [
    'Brand name', 'PURCHASE PRICE IN USD', 'PURCHASE IN AUD',
    'PACKAGE WEIGHT IN GRAMS', 'PACKAGE WEIGHT IN KG', 'Fulfilment Method',
    'Fulfilment Adjustment', 'SELLING PRICE IN AUD', 'OUR APPLIED SELLING PRICE IN AUD',
    'REFERRAL FEE % CRITERIA', 'REFERRAL FEE AUD', 'CLOSING FEE AUD', 'FBA FEE AUD',
    'TOTAL COMMISSION AUD', 'Duties %', 'Duties Charges', 'GST AUD on Selling Price',
    'IMPORT COST LOGISTICS', 'TOTAL COST', 'P & L (AUD)', 'SELLING RANK', 'Amazon AUS', 'Sold by',
]
COLUMNS = INPUTS + CALCULATIONS + ['Status', 'Errors', 'Notes']
ASIN_PATTERN = re.compile(r'^[A-Z0-9]{10}$')
LEGACY_SHEET = 'MATCH ASIN (DEMAND & PRIORITY)'


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    usd_to_aud: float = 1.44
    logistics_aud_per_kg: float = 10.0
    fba_adjustment: float = -1.0
    fbm_adjustment: float = 3.0
    amazon_adjustment: float = -1.0


def key(value):
    return str(value).strip().casefold() if pd.notna(value) else ''


def asin(value):
    return str(value).strip().upper() if pd.notna(value) else ''


def read_table(content: bytes, filename: str, research=False):
    """Read the named working sheet only when given the original workbook."""
    try:
        if filename.lower().endswith('.csv'):
            frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False,
                                encoding='utf-8-sig')
        elif filename.lower().endswith('.xlsx'):
            book = pd.ExcelFile(io.BytesIO(content), engine='openpyxl')
            sheet = LEGACY_SHEET if research and LEGACY_SHEET in book.sheet_names else book.sheet_names[0]
            frame = pd.read_excel(book, sheet_name=sheet, dtype=str, keep_default_na=False)
        else:
            raise InputError(f'{filename}: upload a CSV or XLSX file.')
    except InputError:
        raise
    except Exception as exc:
        raise InputError(f'Could not read {filename}: {exc}') from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    if frame.columns.duplicated().any():
        raise InputError(f'{filename}: duplicate column headers.')
    return frame


def require(frame, columns, label):
    missing = [c for c in columns if c not in frame]
    if missing:
        raise InputError(f'{label}: missing columns: {", ".join(missing)}')


def normalize_research(frame):
    frame = frame.rename(columns={'Product type': 'Sub-category', 'Updated Category': 'Category',
                                  'SUB CATEGORY': 'Sub-category', 'Sub Category': 'Sub-category'})
    require(frame, INPUTS, 'Research list')
    # The original sheet has a second descriptive header row.
    frame = frame[frame['ASIN'].map(key) != 'asin'].copy()
    mask = frame[INPUTS].apply(lambda c: c.map(key)).ne('').any(axis=1)
    frame = frame.loc[mask, INPUTS].reset_index(drop=True)
    frame['ASIN'] = frame['ASIN'].map(asin)
    for c in INPUTS[1:]:
        frame[c] = frame[c].astype(str).str.strip()
    if frame.empty:
        raise InputError('Research list has no product rows. Fill ASIN, Sub-category and Category.')
    return frame


def first_index(frame, label):
    frame = frame.copy()
    frame['asin'] = frame['asin'].map(asin)
    counts = frame['asin'].value_counts().to_dict()
    return frame.drop_duplicates('asin', keep='first').set_index('asin').to_dict('index'), counts


def number(value, label, errors, allow_zero=False):
    try:
        n = float(value)
        if not math.isfinite(n) or (n < 0 if allow_zero else n <= 0):
            raise ValueError
        return n
    except (ValueError, TypeError):
        errors.append(f'{label}: missing or invalid value')
        return math.nan


def referral_fee(price, rule):
    if not math.isfinite(price):
        return math.nan
    if rule['type'] == 'flat':
        return price * rule['rate']
    threshold = rule['threshold_aud']
    if rule['type'] == 'whole_price_threshold':
        return price * (rule['rate_at_or_below'] if price <= threshold else rule['rate_above'])
    if rule['type'] == 'marginal_tiers':
        return min(price, threshold) * rule['rate_at_or_below'] + max(price-threshold, 0) * rule['rate_above']
    raise InputError(f'Unsupported referral rule: {rule["type"]}')


def load_rules():
    referral = json.loads((ROOT / 'referral_fees.json').read_text())
    duty = json.loads((ROOT / 'duty_rates.json').read_text())
    return referral, duty


def calculate(research, us, au, fees, details, settings=Settings(), rules=None, *, require_sales_rank=True):
    for value in (settings.usd_to_aud, settings.logistics_aud_per_kg):
        if not math.isfinite(value) or value <= 0:
            raise InputError('Exchange rate and logistics rate must be positive finite numbers.')
    if not all(math.isfinite(v) for v in (settings.fba_adjustment, settings.fbm_adjustment, settings.amazon_adjustment)):
        raise InputError('Fulfilment adjustments must be finite numbers.')
    research = normalize_research(research)
    require(us, ['asin', 'BuyBoxPrices', 'country', 'currency_code'], 'US prices')
    require(au, ['asin', 'BuyBoxPrices', 'seller_id', 'fulfilled_by_amazon', 'buybox_winner', 'country', 'currency_code'], 'AU prices')
    require(fees, ['asin', 'FBAfees', 'VariableClosingFee', 'currency', 'marketplace'], 'AU fees')
    require(details, ['ASIN', 'Package Weight (g)', 'Sales Rank'], 'Weight and rank')
    referral, duty = rules or load_rules()
    winner_au = au[au['buybox_winner'].map(key) == 'true']
    sources = {}
    for label, frame in [('US', us), ('AU Buy Box', winner_au), ('AU fees', fees),
                         ('Weight/rank', details.rename(columns={'ASIN':'asin'}))]:
        sources[label] = first_index(frame, label)
    research_counts = research['ASIN'].value_counts().to_dict()
    result = []
    for _, item in research.iterrows():
        a = item['ASIN']
        errors, notes, matched = [], [], {}
        if not ASIN_PATTERN.fullmatch(a):
            errors.append('ASIN must contain 10 letters/digits')
        if research_counts[a] > 1:
            notes.append('Repeated research ASIN; input rows preserved')
        for label, (index, counts) in sources.items():
            matched[label] = index.get(a, {})
            if a not in index:
                errors.append(f'{label}: ASIN not found' + (' after buybox_winner=True filter' if label == 'AU Buy Box' else ''))
            elif counts[a] > 1:
                notes.append(f'{label}: first of {counts[a]} matching rows used')
        u, aus, f, d = [matched[n] for n in ['US', 'AU Buy Box', 'AU fees', 'Weight/rank']]
        for record, field, expected, label in [
            (u, 'country', 'us', 'US marketplace'), (u, 'currency_code', 'usd', 'US currency'),
            (aus, 'country', 'au', 'AU marketplace'), (aus, 'currency_code', 'aud', 'AU currency'),
            (f, 'currency', 'aud', 'Fees currency'), (f, 'marketplace', 'aus', 'Fees marketplace')]:
            if record and key(record.get(field)) != expected:
                errors.append(f'{label}: expected {expected.upper()}')
        purchase = number(u.get('BuyBoxPrices'), 'US Buy Box price', errors) if u else math.nan
        selling = number(aus.get('BuyBoxPrices'), 'AU Buy Box price', errors) if aus else math.nan
        weight = number(d.get('Package Weight (g)'), 'Package weight', errors) if d else math.nan
        rank = math.nan
        if d:
            first = str(d.get('Sales Rank', '')).split(',')[0].strip()
            if not first and not require_sales_rank:
                notes.append('Sales rank not supplied')
            elif first.isdigit() and int(first) > 0:
                rank = int(first)
            else:
                errors.append('Sales rank: first value must be a positive integer')
        method, adjustment = '', math.nan
        if aus:
            fulfilled = key(aus.get('fulfilled_by_amazon'))
            if fulfilled == 'true':
                method = 'Amazon' if str(aus.get('seller_id', '')).strip() == 'ANEGB3WVEVKZB' else 'FBA'
                if not str(aus.get('seller_id', '')).strip():
                    errors.append('AU Buy Box: seller_id is missing')
            elif fulfilled == 'false':
                method = 'FBM'
            else:
                errors.append('AU Buy Box: fulfilled_by_amazon must be True or False')
            adjustment = {'Amazon': settings.amazon_adjustment, 'FBA': settings.fba_adjustment,
                          'FBM': settings.fbm_adjustment}.get(method, math.nan)
        applied = selling + adjustment
        if math.isfinite(applied) and applied <= 0:
            errors.append('Adjusted selling price must be positive')
            applied = math.nan
        mapping = referral['category_mappings'].get(key(item['Category']))
        criteria, fee = '', math.nan
        if mapping:
            rule = referral['fee_rules'][mapping['amazon_category']]
            criteria = rule['description']
            fee = referral_fee(applied, rule)
            if 'mapped' in key(mapping['mapping_status']):
                notes.append(f'Category mapping: {mapping["amazon_category"]}; {mapping["mapping_status"]}')
        else:
            errors.append(f'Unknown referral category: {item["Category"] or "blank"}')
        duty_entry = duty['subcategories'].get(key(item['Sub-category']))
        duty_pct = math.nan
        if key(item['Sub-category']) in ('', '#n/a', 'n/a', '0'):
            errors.append('Sub-category is missing or invalid')
        elif duty_entry is None:
            errors.append(f'Unknown duty sub-category: {item["Sub-category"]}')
        else:
            duty_pct = float(duty_entry['duty_percent'])
            if duty_entry.get('conflicting_rates'):
                notes.append('Duty: first source-row rate used despite conflicting later rates')
        closing = number(f.get('VariableClosingFee'), 'Closing fee', errors, True) if f else math.nan
        fba = number(f.get('FBAfees'), 'FBA fee', errors, True) if f else math.nan
        if f and (str(f.get('error', '')).strip() or str(f.get('error_type', '')).strip()):
            errors.append('AU fees: source report contains an API error')
            closing = fba = math.nan
        purchase_aud = purchase * settings.usd_to_aud
        kg = weight / 1000
        commission = fee + closing + fba
        duty_charge = purchase_aud * duty_pct / 100
        gst = applied * 10 / 110
        logistics = kg * settings.logistics_aud_per_kg
        total = purchase_aud + commission + duty_charge + gst + logistics
        profit = applied - total
        values = [d.get('Brand', ''), purchase, purchase_aud, weight, kg, method, adjustment, selling,
                  applied, criteria, fee, closing, fba, commission, duty_pct, duty_charge, gst,
                  logistics, total, profit, rank, f'https://www.amazon.com.au/dp/{a}' if ASIN_PATTERN.fullmatch(a) else '',
                  aus.get('seller_id', '')]
        record = dict(zip(INPUTS + CALCULATIONS, list(item[INPUTS]) + values))
        record.update(Status='Error' if errors else 'Ready', Errors='; '.join(errors), Notes='; '.join(notes))
        result.append(record)
    return pd.DataFrame(result, columns=COLUMNS)


def calculate_single(*, asin_value, category, subcategory, us_price, au_price,
                     fulfilment, package_weight_g, fba_fee, closing_fee,
                     sales_rank='', brand='', settings=Settings(), rules=None):
    """Evaluate manual inputs through the same engine as uploaded reports."""
    methods = {'amazon': 'Amazon', 'fba': 'FBA', 'fbm': 'FBM'}
    method = methods.get(key(fulfilment))
    if method is None:
        raise InputError('Choose Amazon, FBA or FBM fulfilment.')
    a = asin(asin_value)
    if not a:
        raise InputError('Enter an ASIN.')
    research = pd.DataFrame([{'ASIN': a, 'Sub-category': subcategory or '', 'Category': category or ''}])
    us = pd.DataFrame([{'asin': a, 'BuyBoxPrices': us_price, 'country': 'US', 'currency_code': 'USD'}])
    # This internal marker encodes the manually selected third-party method;
    # it is removed from the result so it never appears as a real seller ID.
    au = pd.DataFrame([{'asin': a, 'BuyBoxPrices': au_price, 'country': 'AU', 'currency_code': 'AUD',
                        'seller_id': 'ANEGB3WVEVKZB' if method == 'Amazon' else 'manual-entry',
                        'fulfilled_by_amazon': method != 'FBM', 'buybox_winner': True}])
    fees = pd.DataFrame([{'asin': a, 'FBAfees': fba_fee, 'VariableClosingFee': closing_fee,
                          'currency': 'AUD', 'marketplace': 'AUS'}])
    details = pd.DataFrame([{'ASIN': a, 'Package Weight (g)': package_weight_g,
                             'Sales Rank': '' if sales_rank is None else str(sales_rank).strip(),
                             'Brand': brand or ''}])
    result = calculate(research, us, au, fees, details, settings, rules, require_sales_rank=False)
    result['Sold by'] = 'ANEGB3WVEVKZB' if method == 'Amazon' else ''
    result['Notes'] = result['Notes'].map(lambda n: 'Manual entry; no live Amazon lookup' + ('; ' + n if n else ''))
    valid = result['Status'].eq('Ready')
    result['Profit margin %'] = (result['P & L (AUD)'] / result['OUR APPLIED SELLING PRICE IN AUD'] * 100).where(valid)
    result['ROI % (on total cost)'] = (result['P & L (AUD)'] / result['TOTAL COST'] * 100).where(valid)
    return result


def safe_text(value):
    # Keep imported strings as literal text in spreadsheet downloads.
    return "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


def csv_bytes(frame):
    return frame.map(safe_text).to_csv(index=False).encode('utf-8-sig')


def excel_bytes(frame, metadata=None):
    """User-triggered app export: static, already calculated values."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import CellIsRule
    book = Workbook()
    sheet = book.active
    sheet.title = 'ASIN Results'
    sheet.sheet_view.showGridLines = False
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        sheet.append([None if pd.isna(v) else safe_text(v) for v in row])
    for cell in sheet[1]:
        cell.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='172B4D')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[1].height = 44
    for i, name in enumerate(frame.columns, 1):
        width = 23 if len(name) > 18 else 19
        if name in ['REFERRAL FEE % CRITERIA', 'Errors', 'Notes']:
            width = 58
        sheet.column_dimensions[get_column_letter(i)].width = width
        for cells in sheet.iter_cols(min_col=i, max_col=i, min_row=2):
            for cell in cells:
                cell.font = Font(name='Arial', size=10)
                cell.alignment = Alignment(vertical='center')
                if isinstance(cell.value, (int,float)):
                    cell.number_format = '#,##0' if name == 'SELLING RANK' else '#,##0.00'
    sheet.freeze_panes = 'D2'
    sheet.auto_filter.ref = sheet.dimensions
    if 'P & L (AUD)' in frame and len(frame):
        col = get_column_letter(list(frame.columns).index('P & L (AUD)')+1)
        sheet.conditional_formatting.add(f'{col}2:{col}{len(frame)+1}', CellIsRule(operator='lessThan', formula=['0'], font=Font(color='B42318')))
    if metadata:
        meta = book.create_sheet('Run details')
        meta.append(['Setting', 'Value'])
        for k, v in metadata.items():
            meta.append([safe_text(str(k)), safe_text(str(v))])
        meta.column_dimensions['A'].width = 32
        meta.column_dimensions['B'].width = 95
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def compare_prices(current, previous):
    fields = ['PURCHASE PRICE IN USD', 'SELLING PRICE IN AUD', 'P & L (AUD)']
    require(previous, ['ASIN'] + fields, 'Previous results')
    prev = previous.copy()
    prev['ASIN'] = prev['ASIN'].map(asin)
    prev = prev.drop_duplicates('ASIN', keep='first').set_index('ASIN')
    out = current[['ASIN'] + fields].copy()
    out['Comparison'] = out['ASIN'].map(lambda a: 'Existing' if a in prev.index else 'New ASIN')
    for field in fields:
        old = pd.to_numeric(out['ASIN'].map(prev[field]), errors='coerce')
        new = pd.to_numeric(out[field], errors='coerce')
        out[field + ' — previous'] = old
        out[field + ' — change'] = new - old
    return out
