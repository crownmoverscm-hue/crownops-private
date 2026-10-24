"""Fail-closed rollout and conservative cost reservations. No provider actions."""
import os
from decimal import Decimal,ROUND_CEILING
from fastapi import HTTPException


def spending_policy():
    try:
        ceiling=Decimal(os.environ['AI_MAX_USD_PER_MILLION_TOKENS'])
        budget=int(os.environ['AI_MONTHLY_BUDGET_USD_CENTS'])
        if not ceiling.is_finite() or ceiling<=0 or budget<=0:raise ValueError()
    except (KeyError,ValueError,ArithmeticError):
        raise HTTPException(503,'An owner must configure the AI price ceiling and monthly USD budget before activation.')
    return ceiling,budget


def ensure_cost_column(db):
    if 'reserved_cost_cents' not in {r['name'] for r in db.execute('PRAGMA table_info(ai_runs)')}:
        db.execute('ALTER TABLE ai_runs ADD COLUMN reserved_cost_cents INTEGER NOT NULL DEFAULT 0')


def reserve_cost(db,month,tokens,ceiling,budget):
    cost=max(1,int((Decimal(tokens)*ceiling/Decimal(10000)).quantize(Decimal('1'),rounding=ROUND_CEILING)))
    # Legacy calls lack cost provenance: don't silently treat them as free.
    if db.execute("SELECT count(*) FROM ai_runs WHERE substr(created_at,1,7)=? AND reserved_cost_cents=0",(month,)).fetchone()[0]:
        raise HTTPException(409,'Legacy AI usage needs owner cost reconciliation before more requests.')
    spent=db.execute('SELECT COALESCE(SUM(reserved_cost_cents),0) FROM ai_runs WHERE substr(created_at,1,7)=?',(month,)).fetchone()[0]
    if spent+cost>budget:raise HTTPException(429,'Monthly AI spending budget is fully reserved. No provider request was made.')
    return cost
