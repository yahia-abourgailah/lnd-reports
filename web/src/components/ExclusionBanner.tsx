/**
 * How many records are excluded from every figure on the screen.
 *
 * Shown whether the count is zero or not. A banner that appears only when
 * there is something to report teaches nobody to look for it, and the reading
 * somebody needs — "these numbers cover everything" — is exactly the one that
 * would never be stated.
 *
 * Excluded is not dropped. Every one of these rows is in `ops.dq_exception`
 * with a rule and a reason, and this links to them: the difference between a
 * platform and the workbook is that the workbook could not have told you the
 * number, let alone the rows.
 */

export function ExclusionBanner({ count }: { count: number }) {
  if (count === 0) {
    return (
      <p className="exclusion exclusion-clear">
        <span className="exclusion-dot" aria-hidden="true" />
        Every record was counted. Nothing is excluded from these figures.
      </p>
    )
  }

  return (
    <p className="exclusion exclusion-some">
      <span className="exclusion-dot" aria-hidden="true" />
      <strong>
        {count.toLocaleString()} record{count === 1 ? '' : 's'}
      </strong>{' '}
      could not be placed and {count === 1 ? 'is' : 'are'} excluded from every figure below.
      {' '}They are quarantined, not discarded — each one is kept with the rule it broke.
    </p>
  )
}
