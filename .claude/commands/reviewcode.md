Review the specified script for internal logic and produce a structured assessment.

## Instructions

1. If I don't specify a file, ask which script to review.
2. Read the entire script, then produce a review with these sections:

---

### Script Outline

Provide a plain-English walkthrough of what the script does, structured as a numbered sequence of steps. For each step, note:
- What it reads or receives as input
- What transformation or computation it performs
- What it produces (variables, files, side effects)

### Inputs and Outputs

| Type    | Path / Description                          |
|---------|---------------------------------------------|
| Input   | list every file or data source the script reads |
| Output  | list every file, figure, or table it writes     |

### Logic Review

Check for and flag any of the following:
- **Data handling**: Does it respect raw data as read-only? Are merges/joins correct (correct keys, appropriate join type, handled duplicates)? Are filters applied in the right order?
- **Missing data**: Are NaN/missing values handled explicitly, or could they silently propagate? Are the CLAUDE.md recoding rules followed (PUE=0 → NaN, Land Area=0 → NaN, negative pipeline values → 0)?
- **Scope**: Is the US market filter applied before analysis? Is the correct identifier pair `(Company, Data Center Name)` used?
- **Computation**: Do aggregations, group-bys, and reshapes do what the comments say they do? Any off-by-one errors, wrong axis, or silent broadcasting?
- **Output correctness**: Are figures/tables labeled properly? Are units consistent? Do file paths point to the right output directories?

### Issues Found

List anything that looks wrong, risky, or inconsistent — from outright bugs to things that are technically correct but fragile or unclear. Categorize each as:
- 🔴 **Bug**: will produce wrong results
- 🟡 **Warning**: might produce wrong results under some conditions
- 🔵 **Suggestion**: works but could be clearer or more robust

If nothing is found in a category, say so explicitly.

### Summary

One paragraph: is this script ready to run, or does it need changes first?

---

3. Do NOT modify the script. This command is read-only — review and report only.
