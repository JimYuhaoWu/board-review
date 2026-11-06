## Gate 1b: Qwen, profile core

| Tool | claude-opus-4-5-20251101 | claude-sonnet-4-5-20250929 | gpt-5.2 | Qwen |
|---|---|---|---|---|
| cubic-v2 | 63.6% | 58.5% | 58.7% | 59.4% |
| augment | 62.2% | 56.6% | 54.5% | 57.3% |
| macroscope | 54.2% | 46.2% | 44.7% | 45.2% |
| devin | 51.6% | 51.6% | 48.3% | 50.8% |
| claude-code | 47.2% | 38.7% | 40.6% | 39.5% |
| codeant-v2 | 40.6% | 33.5% | 32.7% | 35.4% |
| kg | 24.5% | 21.8% | 23.8% | 25.2% |

Kendall tau of the tool rankings (F1):

- Qwen vs claude-opus-4-5-20251101: +0.90
- Qwen vs claude-sonnet-4-5-20250929: +1.00
- Qwen vs gpt-5.2: +1.00
- claude-opus-4-5-20251101 vs claude-sonnet-4-5-20250929: +0.90
- claude-opus-4-5-20251101 vs gpt-5.2: +0.90
- claude-sonnet-4-5-20250929 vs gpt-5.2: +1.00

Mean tau with the published judges +0.97; lowest tau between two published judges +0.90. **Pass.**

Repeat check: 200 cached judge prompts asked again; same verdict 200, changed 0, failed 0 (agreement 100.0%).
