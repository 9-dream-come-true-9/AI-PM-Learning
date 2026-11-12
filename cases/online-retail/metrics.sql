SELECT
    month,
    SUM(CASE WHEN kind = 'sale' THEN amount_pence ELSE 0 END) AS gross_pence,
    SUM(CASE WHEN kind = 'cancel' THEN amount_pence ELSE 0 END) AS cancelled_pence,
    SUM(CASE WHEN kind = 'sale' THEN amount_pence ELSE -amount_pence END) AS net_pence,
    COUNT(DISTINCT CASE WHEN kind = 'sale' THEN invoice END) AS sale_invoices
FROM lines
WHERE month IN (?, ?)
GROUP BY month
ORDER BY month;
