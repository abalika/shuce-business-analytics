-- Rate of the totals, not average of per-row rates. Zero views means undefined.
SELECT SUM(views) AS views, SUM(clicks) AS clicks,
       CASE WHEN SUM(views) = 0 THEN NULL ELSE 100.0 * SUM(clicks) / SUM(views) END AS ctr
FROM facts;
