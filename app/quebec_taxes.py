"""Cent-exact non-compounded tax expectation for reviewed taxable Québec moves."""
from decimal import Decimal, ROUND_HALF_UP


def taxable_move(subtotal_cents):
    if type(subtotal_cents) is not int or subtotal_cents<0:
        raise ValueError('Taxable subtotal must be a nonnegative whole cent amount.')
    gst=int((Decimal(subtotal_cents)*Decimal('.05')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    qst=int((Decimal(subtotal_cents)*Decimal('.09975')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return {'subtotal_cents':subtotal_cents,'gst_cents':gst,'qst_cents':qst,
            'tax_cents':gst+qst,'total_cents':subtotal_cents+gst+qst}
