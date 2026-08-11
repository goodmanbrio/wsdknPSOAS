# Leng Eval — 2026-07-27 21:23:56

**Profiles**: `deepseek_v4pro_leng`, `deepseek_v4flash_leng`

**Cases**: 8

---

## Provider: `deepseek_v4pro_leng`

Model: `deepseek-v4-pro`

### lite_call5_mizuho_overview (17.0s, ~$0.0006)

```json
{
  "found": true,
  "cells": {
    "A1": {
      "value": 17163,
      "denom": "mn",
      "unit": "USD"
    },
    "A5": {
      "value": 242.07,
      "denom": "units",
      "unit": "USD"
    }
  }
}
```

- A1: OK
- A5: OK

### lite_call24_mizuho_estimates (21.8s, ~$0.0006)

```json
{
  "found": true,
  "cells": {
    "B6": {
      "value": 5.78,
      "denom": "units",
      "unit": "USD"
    }
  }
}
```

- B6: OK
- B7: MISSED (false negative)

### lite_call1_risks_miss (3.3s, ~$0.0004)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call64_balance_sheet (16.8s, ~$0.0005)

```json
{
  "found": false,
  "cells": {}
}
```


### innolight_call20_crshk_cover (12.6s, ~$0.0012)

```json
{
  "found": true,
  "cells": {
    "A3": {
      "value": 973,
      "denom": "mn",
      "unit": "CNY"
    },
    "B3": {
      "value": 1540,
      "denom": "mn",
      "unit": "CNY"
    }
  }
}
```

- A7: MISSED (false negative)
- B7: MISSED (false negative)
- A3: OK
- B3: OK

### innolight_call19_datacenter_miss (4.7s, ~$0.0004)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call23_cpo_miss (5.0s, ~$0.0004)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call2_toc_miss (4.4s, ~$0.0005)

```json
{
  "found": false,
  "cells": {}
}
```


### Summary: `deepseek_v4pro_leng`

| Metric     | Value |
|------------|-------|
| Cases      | 8 |
| Errors     | 0 |
| Precision  | 1.00 |
| Recall     | 0.62 |
| Denom acc  | 1.00 |
| Avg cost   | $0.0006/case |
| Avg latency| 10.7s |

| Case | Expected | Found | TP | FP | FN | Notes |
|------|----------|-------|----|----|----|-------|
| lite_call5_mizuho_overview | 2 | 2 | 2 | 0 | 0 | A1: OK; A5: OK |
| lite_call24_mizuho_estimates | 2 | 1 | 1 | 0 | 1 | B6: OK; B7: MISSED (false negative) |
| lite_call1_risks_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call64_balance_sheet | 0 | 0 | 0 | 0 | 0 |  |
| innolight_call20_crshk_cover | 4 | 2 | 2 | 0 | 2 | A7: MISSED (false negative); B7: MISSED (false negative); A3: OK |
| innolight_call19_datacenter_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call23_cpo_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call2_toc_miss | 0 | 0 | 0 | 0 | 0 |  |

---

## Provider: `deepseek_v4flash_leng`

Model: `deepseek-v4-flash`

### lite_call5_mizuho_overview (16.4s, ~$0.0002)

```json
{
  "found": true,
  "cells": {
    "A1": {
      "value": 17163,
      "denom": "mn",
      "unit": "USD"
    },
    "A5": {
      "value": 242.07,
      "denom": "units",
      "unit": "USD"
    }
  }
}
```

- A1: OK
- A5: OK

### lite_call24_mizuho_estimates (17.9s, ~$0.0002)

```json
{
  "found": true,
  "cells": {
    "B6": {
      "value": 5.78,
      "denom": "units",
      "unit": "USD"
    },
    "B10": {
      "value": 50.2,
      "denom": "units",
      "unit": "float"
    }
  }
}
```

- B6: OK
- B7: MISSED (false negative)
- B10: FALSE POSITIVE (value=50.2)

### lite_call1_risks_miss (2.7s, ~$0.0001)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call64_balance_sheet (27.5s, ~$0.0002)

```json
{
  "found": false,
  "cells": {}
}
```


### innolight_call20_crshk_cover (9.7s, ~$0.0004)

```json
{
  "found": true,
  "cells": {
    "A3": {
      "value": 973,
      "denom": "mn",
      "unit": "CNY"
    },
    "B3": {
      "value": 1540,
      "denom": "mn",
      "unit": "CNY"
    }
  }
}
```

- A7: MISSED (false negative)
- B7: MISSED (false negative)
- A3: OK
- B3: OK

### innolight_call19_datacenter_miss (2.8s, ~$0.0001)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call23_cpo_miss (7.4s, ~$0.0001)

```json
{
  "found": false,
  "cells": {}
}
```


### lite_call2_toc_miss (2.7s, ~$0.0002)

```json
{
  "found": false,
  "cells": {}
}
```


### Summary: `deepseek_v4flash_leng`

| Metric     | Value |
|------------|-------|
| Cases      | 8 |
| Errors     | 0 |
| Precision  | 0.83 |
| Recall     | 0.62 |
| Denom acc  | 1.00 |
| Avg cost   | $0.0002/case |
| Avg latency| 10.9s |

| Case | Expected | Found | TP | FP | FN | Notes |
|------|----------|-------|----|----|----|-------|
| lite_call5_mizuho_overview | 2 | 2 | 2 | 0 | 0 | A1: OK; A5: OK |
| lite_call24_mizuho_estimates | 2 | 2 | 1 | 1 | 1 | B6: OK; B7: MISSED (false negative); B10: FALSE POSITIVE (value=50.2) |
| lite_call1_risks_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call64_balance_sheet | 0 | 0 | 0 | 0 | 0 |  |
| innolight_call20_crshk_cover | 4 | 2 | 2 | 0 | 2 | A7: MISSED (false negative); B7: MISSED (false negative); A3: OK |
| innolight_call19_datacenter_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call23_cpo_miss | 0 | 0 | 0 | 0 | 0 |  |
| lite_call2_toc_miss | 0 | 0 | 0 | 0 | 0 |  |

---

## Cross-provider comparison

| Provider | Precision | Recall | Denom Acc | Avg Cost | Avg Latency |
|----------|-----------|--------|-----------|----------|-------------|
| deepseek_v4pro_leng | 1.00 | 0.62 | 1.00 | $0.0006 | 10.7s |
| deepseek_v4flash_leng | 0.83 | 0.62 | 1.00 | $0.0002 | 10.9s |

