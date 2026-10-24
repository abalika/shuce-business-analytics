-- Parameters are [previous_start, previous_end, current_start, current_end].
WITH changes AS (
  SELECT dimension,
    SUM(CASE WHEN date BETWEEN :previous_start AND :previous_end THEN amount ELSE 0 END) AS previous_cents,
    SUM(CASE WHEN date BETWEEN :start AND :end THEN amount ELSE 0 END) AS current_cents
  FROM facts GROUP BY dimension
)
SELECT dimension, previous_cents, current_cents, current_cents-previous_cents AS change_cents
FROM changes
WHERE previous_cents <> 0 OR current_cents <> 0
ORDER BY ABS(current_cents-previous_cents) DESC, dimension;
