SELECT SUBSTR(date, 1, 7) AS month, SUM(amount) AS net_cents
FROM facts
GROUP BY SUBSTR(date, 1, 7)
ORDER BY month;
