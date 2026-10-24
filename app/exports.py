"""CSV exports with explicit units and spreadsheet formula protection."""
import pandas as pd
from decimal import Decimal,InvalidOperation
from fastapi.responses import Response

def csv_response(frame: pd.DataFrame, filename: str):
    safe=frame.copy()
    for col in safe.columns:
        if safe[col].dtype.kind in {"O","U","S"}:
            def cell(x):
                if col in {"quantity","unit_price"}:
                    try:
                        if Decimal(str(x)).is_finite():return x
                    except InvalidOperation:pass
                return "'"+str(x) if str(x).startswith(("=","+","-","@","\t","\r")) else x
            safe[col]=safe[col].map(cell)
    safe=safe.rename(columns={"amount":"net_amount_cents","positive_amount":"positive_amount_cents","negative_amount":"negative_amount_cents"})
    return Response("\ufeff"+safe.to_csv(index=False),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{filename}"'})
