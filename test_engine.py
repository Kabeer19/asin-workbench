import io
import math
import unittest
import pandas as pd
import openpyxl
from engine import (calculate, Settings, referral_fee, load_rules, excel_bytes,
                    normalize_research, compare_prices, read_table, InputError, csv_bytes)


def fixture():
    """Synthetic offers designed to expose first-match and fee-boundary errors."""
    ids=['B000000001','B000000002','B000000003']
    research=pd.DataFrame({'ASIN':ids,'Sub-category':['FOOD_SLICER','PRINTER','HEADPHONES'],
                           'Category':['Kitchen','PC','Electronics']})
    us=pd.DataFrame([{'asin':a,'BuyBoxPrices':'20','country':'US','currency_code':'USD',
                      'buybox_winner':'False','fulfilled_by_amazon':'False'} for a in ids])
    au=pd.DataFrame([{'asin':a,'BuyBoxPrices':p,'seller_id':s,'fulfilled_by_amazon':f,
                      'buybox_winner':'True','country':'AU','currency_code':'AUD'}
                     for a,p,s,f in zip(ids,['66','200','70'],['ANEGB3WVEVKZB','OTHER','THIRD'],['True','True','False'])])
    fees=pd.DataFrame([{'asin':a,'FBAfees':'8.88','VariableClosingFee':'0','currency':'AUD',
                        'marketplace':'AUS','ReferralFee':'999999','total_fees':'999999'} for a in ids])
    details=pd.DataFrame([{'ASIN':a,'Package Weight (g)':'750','Sales Rank':'5588, 55',
                           'Brand':'Example'} for a in ids])
    return research,us,au,fees,details


class EngineTests(unittest.TestCase):
    def test_all_methods_and_full_calculation(self):
        result=calculate(*fixture())
        self.assertEqual(result.Status.tolist(),['Ready']*3)
        self.assertEqual(result['Fulfilment Method'].tolist(),['Amazon','FBA','FBM'])
        self.assertEqual(result['Fulfilment Adjustment'].tolist(),[-1,-1,3])
        r=result.iloc[0]
        self.assertEqual(r['SELLING RANK'],5588)
        self.assertEqual(r['PACKAGE WEIGHT IN GRAMS'],750)
        self.assertAlmostEqual(r['PACKAGE WEIGHT IN KG'],.75)
        self.assertAlmostEqual(r['REFERRAL FEE AUD'],65*.13)
        expected=28.8 + 65*.13 + 8.88 + 28.8*r['Duties %']/100 + 65*10/110 + 7.5
        self.assertAlmostEqual(r['TOTAL COST'],expected)
        self.assertAlmostEqual(r['P & L (AUD)'],65-expected)
        # PC fees are marginal: first 150 at 8%, remainder at 6%.
        self.assertAlmostEqual(result.iloc[1]['REFERRAL FEE AUD'],150*.08+49*.06)

    def test_no_us_filter_and_first_match_from_same_au_row(self):
        research,us,au,fees,details=fixture()
        us=pd.concat([us,us.assign(BuyBoxPrices='999')],ignore_index=True)
        ignored=au.iloc[[0]].assign(BuyBoxPrices='999',buybox_winner='False')
        later=au.iloc[[0]].assign(BuyBoxPrices='900',seller_id='OTHER',fulfilled_by_amazon='False')
        au=pd.concat([ignored,au,later],ignore_index=True)
        r=calculate(research,us,au,fees,details).iloc[0]
        self.assertEqual(r['PURCHASE PRICE IN USD'],20)
        self.assertEqual(r['SELLING PRICE IN AUD'],66)
        self.assertEqual(r['Fulfilment Method'],'Amazon')
        self.assertIn('first of 2',r.Notes)

    def test_first_duty_subcategory_ignores_main_category(self):
        referral,duty=load_rules()
        research,us,au,fees,details=fixture()
        conflict=next((k,v) for k,v in duty['subcategories'].items() if v['conflicting_rates'] and k!='0')
        research.loc[0,'Sub-category']=conflict[1]['label']
        r=calculate(research,us,au,fees,details).iloc[0]
        self.assertEqual(r['Duties %'],conflict[1]['duty_percent'])
        self.assertIn('conflicting later rates',r.Notes)

    def test_whole_price_and_marginal_thresholds(self):
        rules=load_rules()[0]['fee_rules']
        beauty=rules['Beauty, Health, and Personal Care']
        self.assertAlmostEqual(referral_fee(20,beauty),1.6)
        self.assertAlmostEqual(referral_fee(20.01,beauty),20.01*.13)
        pc=rules['Computers']
        self.assertAlmostEqual(referral_fee(150,pc),12)
        self.assertAlmostEqual(referral_fee(200,pc),15)
        jewelry=rules['Jewellery']
        self.assertAlmostEqual(referral_fee(300,jewelry),35)

    def test_price_refresh_changes_fee_and_profit(self):
        inputs=list(fixture())
        before=calculate(*inputs)
        inputs[1]=inputs[1].assign(BuyBoxPrices='30')
        after=calculate(*inputs)
        self.assertAlmostEqual(after.iloc[0]['PURCHASE IN AUD']-before.iloc[0]['PURCHASE IN AUD'],14.4)
        duty=before.iloc[0]['Duties %']/100
        self.assertAlmostEqual(after.iloc[0]['P & L (AUD)']-before.iloc[0]['P & L (AUD)'],-14.4*(1+duty))
        inputs[2]=inputs[2].assign(BuyBoxPrices='76')
        sold=calculate(*inputs)
        self.assertNotEqual(sold.iloc[0]['REFERRAL FEE AUD'],after.iloc[0]['REFERRAL FEE AUD'])

    def test_missing_price_never_becomes_zero_or_later_match(self):
        research,us,au,fees,details=fixture()
        us.loc[0,'BuyBoxPrices']=''
        us=pd.concat([us,us.iloc[[0]].assign(BuyBoxPrices='50')],ignore_index=True)
        r=calculate(research,us,au,fees,details).iloc[0]
        self.assertEqual(r.Status,'Error')
        self.assertTrue(math.isnan(r['PURCHASE PRICE IN USD']))
        self.assertTrue(math.isnan(r['P & L (AUD)']))

    def test_missing_offer_fee_weight_rank_and_unknown_categories(self):
        research,us,au,fees,details=fixture()
        au.loc[0,'buybox_winner']='False'
        fees.loc[1,'FBAfees']=''
        details.loc[2,'Package Weight (g)']='0'
        details.loc[2,'Sales Rank']='N/A'
        research.loc[2,'Category']='unknown'
        research.loc[2,'Sub-category']='unknown'
        result=calculate(research,us,au,fees,details)
        self.assertEqual(result.Status.tolist(),['Error']*3)
        self.assertTrue(result['P & L (AUD)'].isna().all())
        self.assertIn('after buybox_winner=True',result.iloc[0].Errors)
        self.assertIn('Unknown referral category',result.iloc[2].Errors)

    def test_zero_fees_valid_and_wrong_currency_is_error(self):
        research,us,au,fees,details=fixture()
        fees=fees.assign(FBAfees='0',VariableClosingFee='0')
        result=calculate(research,us,au,fees,details)
        self.assertTrue(result.Status.eq('Ready').all())
        us.loc[0,'currency_code']='EUR'
        self.assertIn('expected USD',calculate(research,us,au,fees,details).iloc[0].Errors)

    def test_duplicate_inputs_preserved_and_category_normalized(self):
        research,us,au,fees,details=fixture()
        research.loc[0,'Category']='  KITCHEN  '
        research=pd.concat([research,research.iloc[[0]]],ignore_index=True)
        result=calculate(research,us,au,fees,details)
        self.assertEqual(len(result),4)
        self.assertTrue(result.Status.eq('Ready').all())

    def test_legacy_headers_and_empty_template(self):
        legacy=pd.DataFrame({'ASIN':['ASIN','B000000001'],'Product type':['Product type','PRINTER'],
                             'Updated Category':['Updated Category','PC']})
        self.assertEqual(len(normalize_research(legacy)),1)
        with self.assertRaises(InputError):
            normalize_research(pd.DataFrame(columns=['ASIN','Sub-category','Category']))

    def test_download_round_trip_and_compare_by_asin(self):
        result=calculate(*fixture())
        output=excel_bytes(result,{'USD → AUD':1.44})
        book=openpyxl.load_workbook(io.BytesIO(output),data_only=True)
        self.assertEqual(book['ASIN Results'].max_row,4)
        self.assertEqual(book['ASIN Results']['A2'].value,'B000000001')
        previous=read_table(output,'previous.xlsx')
        comparison=compare_prices(result.iloc[::-1],previous)
        self.assertTrue(comparison.filter(like='— change').abs().lt(1e-8).all().all())
        frame=pd.DataFrame({'ASIN':['=1+2']})
        self.assertIn("'=1+2",csv_bytes(frame).decode('utf-8-sig'))

    def test_bad_schema_and_settings(self):
        inputs=list(fixture())
        inputs[1]=inputs[1].drop(columns='BuyBoxPrices')
        with self.assertRaisesRegex(InputError,'missing columns'):
            calculate(*inputs)
        with self.assertRaises(InputError):
            calculate(*fixture(),Settings(usd_to_aud=0))


if __name__=='__main__':
    unittest.main()
