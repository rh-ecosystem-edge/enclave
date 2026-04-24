# Comparison of timings splitting oc-mirror into core and post

## Scenarios to test
- A - No change
- B - Split core - post installation mirror
- C - Split mirror using custom oc-mirror
- D - Split mirror + custom oc-mirror + Operators refactor
- E - Split mirror + custom oc-mirror + split quay oc-mirror
- F - Only split quay

## Table of runs
Runs are sorted by time, so Run 1 is always the shorter and Run 3 always the longest of each type

| | A. No change| B. Split mirror | C. B + custom oc-mirror| D. C + operators refactor  | E. B + split quay | F. Split quay |
| --- | --- | --- | --- | --- | --- | --- |
| Run 1 | 155 min | 152 min | 134 min | 134 min | 124 min | 143 min |
| Run 2 | 160 min | 153 min | 141 min | 148 min | 127 min | 143 min |
| Run 3 | 195 min | 164 min | 148 min | 156 min | 127 min | 158 min |
