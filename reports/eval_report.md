# Text-to-SQL evaluation

Engine `offline-planner` on `sqlite`

| Metric | Value |
|---|---|
| cases | 24 |
| execution_accuracy | 1.0 |
| first_try_success | 1.0 |

| id | correct | question | note |
|---|---|---|---|
| rev-total-2025 | yes | What was total revenue in 2025? |  |
| rev-region-2025 | yes | Revenue by region in 2025 |  |
| top5-customers | yes | Top 5 customers by revenue |  |
| monthly-trend-2025 | yes | Show the monthly revenue trend for 2025 |  |
| orders-plant-2026 | yes | How many orders did each plant get in 2026? |  |
| worst-ontime | yes | Which transporter has the worst on-time delivery rate? |  |
| ontime-by-transporter | yes | On-time delivery % by transporter |  |
| pune-march-2026 | yes | How many orders did the Pune Plant get in March 2026? |  |
| freight-transporter | yes | Freight cost by transporter |  |
| aov-segment | yes | Average order value by customer segment |  |
| pumps-quarterly | yes | Revenue from pumps by quarter |  |
| late-omsai-2025 | yes | How many late shipments did Om Sai Transport have in 2025? |  |
| top-category-q1 | yes | Which category sold the most units in Q1 2026? |  |
| units-category | yes | Units sold by product category |  |
| north-category-2025 | yes | Revenue by category in the North region in 2025 |  |
| cancelled-orders | yes | How many orders were cancelled? |  |
| transit-days | yes | Average transit days by transporter |  |
| s1-base | yes | Revenue by region in 2025 |  |
| s1-split | yes | now split that by category |  |
| s1-filter | yes | only the West region |  |
| s2-base | yes | Top 3 customers by number of orders in 2025 |  |
| s2-metric | yes | what about revenue? |  |
| refuse-offtopic | yes | What is the weather in Delhi today? | expected a refusal |
| refuse-fanout | yes | Freight cost by product | expected a refusal |
