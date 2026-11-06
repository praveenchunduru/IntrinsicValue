"""Independent arithmetic reconstruction; no IntrinsicValue imports."""
import copy,hashlib,json,math
from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=json.loads((R/'assumptions.json').read_text());v=json.loads((R/'valuation.json').read_text())
checks=[]
def close(label,a,b,rtol=1e-9,atol=1e-6):
    assert math.isclose(a,b,rel_tol=rtol,abs_tol=atol),(label,a,b)
    checks.append(label)
def rate(spec):
    value=lambda k:spec[k]['value']
    e=value('market_equity');debt=value('financial_debt')+value('finance_lease_debt');tax=value('marginal_tax_rate')
    beta=spec['unlevered_beta']
    if beta.get('calculation')=='historical_issuer_cash_corrected':
        asset=(beta['historical_equity_beta']/(1+(1-tax)*debt/e))/(1-p['net_debt_bridge']['selected_cash_and_nonoperating_investments']/(e+debt))
    else:asset=beta['value']
    cost=value('risk_free_rate')+asset*(1+(1-tax)*debt/e)*value('equity_risk_premium')
    return (e*cost+debt*value('pretax_cost_of_debt')*(1-tax))/(e+debt)
w=rate(p['discount_rate']);close('preferred WACC',w,v['discount_rate_calculation']['applied_wacc'])
actual=p['actuals'];shares=actual['shares_outstanding'];bridge=-actual['net_debt']-actual['other_equity_claims']
def op_arrays(name,override=None):
    op=copy.deepcopy(p['operating_model']['scenarios'][name])
    if override:
        for cat,entries in override.get('components',{}).items():op['components'][cat].update(entries)
        op['expenses'].update(override.get('expenses',{}))
    revs={c['name']:c['base_revenue'] for c in p['operating_model']['components']};out=[]
    n=len(next(iter(op['components'].values()))['growth_rates'])
    for i in range(n):
        gp=0
        for cat,assume in op['components'].items():
            revs[cat]*=1+assume['growth_rates'][i];gp+=revs[cat]*assume['gross_margins'][i]
        out.append((sum(revs.values()),gp-sum(a[i] for a in op['expenses'].values())))
    return out

def dcf(label,inputs,operating,out):
    tax=inputs['tax_rate'];sc=inputs['sales_to_capital'];g=inputs['terminal_growth'];roic=inputs['terminal_roic'];discount=inputs.get('wacc',w)
    previous=actual['revenue'];fcfs=[];pv=0
    for i,(rev,ebit) in enumerate(operating,1):
        fcf=ebit*(1-tax)-(rev-previous)/sc;pv+=fcf/(1+discount)**i;fcfs.append(fcf);previous=rev
        close(f'{label} FY{i} revenue',rev,out['cashflows'][i-1]['revenue'])
        close(f'{label} FY{i} EBIT',ebit,out['cashflows'][i-1]['ebit'])
        close(f'{label} FY{i} FCFF',fcf,out['cashflows'][i-1]['fcff'])
    term=operating[-1][1]*(1+g)*(1-tax)*(1-g/roic)/(discount-g)
    total=pv+term/(1+discount)**len(operating)
    close(label+' value',(total+bridge)/shares,out['per_share_value'])
    return fcfs,term

for name,inputs in p['scenarios'].items():
    out=v['scenarios'][name];fcfs,terminal=dcf(name,inputs,op_arrays(name),out)
    for kind,price in [('market_price',p['market_price']),('model_value',out['per_share_value'])]:
        result=out['fixed_exit_return_at_'+kind];cash=[-price+bridge/shares]+[x/shares for x in fcfs];cash[-1]+=terminal/shares
        for i,(a,b) in enumerate(zip(cash,result['cashflows_per_share'])):close(f'{name} {kind} cash {i}',a,b)
        for irr in result['annual_return_solutions']:
            close(f'{name} {kind} IRR NPV',sum(x/(1+irr)**i for i,x in enumerate(cash)),0,atol=1e-7)
for name,override in p['counterfactuals'].items():
    inputs=copy.deepcopy(p['scenarios']['base']);inputs.update({k:x for k,x in override.items() if k!='operating_overrides'})
    operating=op_arrays('base',override.get('operating_overrides'))
    if 'growth_rates' in inputs:
        rev=actual['revenue'];changed=[]
        for i,g in enumerate(inputs['growth_rates']):
            rev*=1+g;changed.append((rev,rev*(operating[i][1]/operating[i][0])))
        operating=changed
    dcf('counterfactual '+name,inputs,operating,v['counterfactuals'][name])
for comparison in p['calibration_comparisons']:
    spec=copy.deepcopy(p['discount_rate']);spec.update(comparison['overrides']);new=rate(spec)
    out=next(x for x in v['calibration_comparisons'] if x['name']==comparison['name']);close(comparison['name']+' WACC',new,out['wacc'])
for row in v['monitoring']:
    choices=v['counterfactuals'] if 'counterfactual' in row else {x['name']:x for x in v['calibration_comparisons']}
    key=row.get('counterfactual',row.get('calibration_comparison'));close(row['name']+' monitored value',row['conditional_per_share_value'],choices[key]['per_share_value'])
report={'scope':'Independent arithmetic reconstruction without importing runner implementation; does not validate forecast plausibility or source truth.','passed_checks':len(checks),'findings':[],'reviewed_file_sha256':{n:hashlib.sha256((R/n).read_bytes()).hexdigest() for n in ['assumptions.json','valuation.json']}}
(R/'independent-financial-identities.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
