| project | config | runs | op kind | caller | claims P / R | deps P / R | deps relaxed P / R | exits P / R |
|---|---|---|---|---|---|---|---|---|
| beefy-erc4626 | rules | 1 | 0.83 | 1.00 | 1.00 / 1.00 | 0.67 / 1.00 | 0.80 / 1.00 | 1.00 / 1.00 |
| beefy-erc4626 | llm-only | 3 | 1.00 | 1.00 | 1.00 / 1.00 | 0.09 (0.00–0.14) / 0.17 (0.00–0.25) | 0.29 (0.25–0.33) / 0.42 (0.25–0.50) | 0.44 (0.36–0.50) / 1.00 |
| beefy-erc4626 | combined | 3 | 1.00 | 1.00 | 1.00 / 1.00 | 0.42 (0.36–0.44) / 1.00 | 0.51 (0.40–0.57) / 1.00 | 1.00 / 1.00 |
| beefy-erc4626 | combined+checked | 3 | 1.00 | 1.00 | 1.00 / 1.00 | 0.58 (0.50–0.67) / 1.00 | 0.76 (0.67–0.80) / 1.00 | 1.00 / 1.00 |
| kaia-yield | rules | 1 | 1.00 | 0.97 | 0.62 / 0.62 | 0.60 / 0.75 | 0.70 / 0.88 | 0.73 / 0.67 |
| kaia-yield | llm-only | 3 | 0.97 (0.95–1.00) | 0.97 | 0.86 / 0.75 | 0.00 / 0.00 | 0.00 / 0.00 | 0.54 (0.29–0.67) / 1.00 |
| kaia-yield | combined | 3 | 1.00 | 0.97 | 0.60 (0.55–0.67) / 0.75 | 0.37 (0.35–0.38) / 0.75 | 0.44 (0.41–0.47) / 0.88 | 0.73 / 0.67 |
| kaia-yield | combined+checked | 3 | 1.00 | 0.97 | 0.60 (0.55–0.67) / 0.75 | 0.42 (0.40–0.46) / 0.75 | 0.50 (0.47–0.54) / 0.88 | 0.73 / 0.67 |
| metamorpho | rules | 1 | 1.00 | 1.00 | 1.00 / 0.33 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.40 |
| metamorpho | llm-only | 3 | 1.00 | 1.00 | 1.00 / 0.67 | 0.06 (0.00–0.17) / 0.17 (0.00–0.50) | 0.28 (0.25–0.33) / 0.67 (0.50–1.00) | 0.69 (0.62–0.83) / 1.00 |
| metamorpho | combined | 3 | 1.00 | 1.00 | 0.39 (0.33–0.50) / 0.33 | 0.41 (0.33–0.50) / 1.00 | 0.52 (0.40–0.67) / 1.00 | 1.00 / 0.40 |
| metamorpho | combined+checked | 3 | 1.00 | 1.00 | 0.50 / 0.33 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.40 |

Relations and kinds the LLM stage changed relative to rules only (summed over runs):

| project | config | runs | correct added | wrong added | correct lost | kinds fixed | kinds broken |
|---|---|---|---|---|---|---|---|
| beefy-erc4626 | combined | 3 | 0 | 14 | 0 | 9 | 0 |
| beefy-erc4626 | combined+checked | 3 | 0 | 6 | 0 | 9 | 0 |
| kaia-yield | combined | 3 | 3 | 31 | 0 | 0 | 0 |
| kaia-yield | combined+checked | 3 | 3 | 19 | 0 | 0 | 0 |
| metamorpho | combined | 3 | 0 | 20 | 0 | 0 | 0 |
| metamorpho | combined+checked | 3 | 0 | 6 | 0 | 0 | 0 |
