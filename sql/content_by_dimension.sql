SELECT dimension, SUM(views) AS views, SUM(clicks) AS clicks,
       CASE WHEN SUM(views) = 0 THEN NULL ELSE 100.0 * SUM(clicks) / SUM(views) END AS ctr
FROM facts GROUP BY dimension ORDER BY dimension;
