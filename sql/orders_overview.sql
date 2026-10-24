-- Amounts are integer cents; count only positive invoice IDs, not line items.
SELECT SUM(amount) AS net_cents,
       SUM(positive_amount) AS positive_cents,
       SUM(negative_amount) AS negative_cents,
       COUNT(DISTINCT CASE WHEN amount > 0 THEN entity END) AS positive_orders
FROM facts;
