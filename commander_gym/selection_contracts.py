"""Native conditional selection minima, independent of card text or strategy."""
from collections.abc import Mapping


def conditional_minima(p):
    rows=p.get('conditionalMinimums',[])
    if rows is None:rows=[]
    if not isinstance(rows,list):raise ValueError('native conditional selection minima are malformed')
    result=[]
    for row in rows:
        if (not isinstance(row,Mapping) or set(row)-{'requiredSelections','minimumSelections','matchingOptions','requiredMatches','description'}
            or type(row.get('requiredSelections')) is not int or type(row.get('minimumSelections')) is not int
            or not 0<=row['minimumSelections']<=row['requiredSelections']
            or type(row.get('requiredMatches',1)) is not int or row.get('requiredMatches',1)<0
            or not isinstance(row.get('matchingOptions'),list)
            or any(not isinstance(i,str) or not i for i in row['matchingOptions'])):
            raise ValueError('native conditional selection minimum is malformed')
        result.append(row)
    return result


def conditional_selection_valid(ids,p):
    unmet=[row for row in conditional_minima(p) if len(ids)<row['requiredSelections']]
    return not unmet or any(len(ids)>=row['minimumSelections']
        and sum(i in row['matchingOptions'] for i in ids)>=row.get('requiredMatches',1) for row in unmet)


def validate_conditional_selection(ids,p):
    if not conditional_selection_valid(ids,p):raise ValueError('selected cards do not satisfy the native conditional minimum')
