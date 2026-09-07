/**
 * What the data-quality queue costs the figures on screen — and what it does not.
 *
 * Two very different things share that queue and conflating them is the whole
 * reason this component is careful. A session whose times will not subtract is
 * excluded from both hour metrics; a programme over its capacity is counted in
 * full and is merely worth knowing. Reporting both as losses understates the
 * platform's coverage (FR-F04).
 *
 * The first version did exactly that. It announced "3 records could not be
 * placed and are excluded from every figure below" when nothing was excluded
 * at all — all three were flagged and counted. That is worse than saying
 * nothing: it is wrong in the direction that sounds careful, and a reader who
 * trusts it now distrusts figures that were complete.
 *
 * Shown at zero as well as above it. A banner that appears only when there is
 * something to report teaches nobody to look for it, and "these numbers cover
 * everything" is exactly the reading that would then never be stated.
 */

export function ExclusionBanner({
  excluded,
  flagged,
}: {
  excluded: number
  flagged: number
}) {
  const plural = (n: number) => (n === 1 ? '' : 's')

  if (excluded === 0) {
    return (
      <p className="exclusion exclusion-clear">
        <span className="exclusion-dot" aria-hidden="true" />
        <span>
          Every record is counted. Nothing is excluded from these figures.
          {flagged > 0 && (
            <span className="exclusion-aside">
              {' '}
              {flagged.toLocaleString()} record{plural(flagged)} {flagged === 1 ? 'is' : 'are'}{' '}
              flagged for review and still counted in full.
            </span>
          )}
        </span>
      </p>
    )
  }

  return (
    <p className="exclusion exclusion-some">
      <span className="exclusion-dot" aria-hidden="true" />
      <span>
        <strong>
          {excluded.toLocaleString()} record{plural(excluded)}
        </strong>{' '}
        {excluded === 1 ? 'is' : 'are'} excluded from the figures {excluded === 1 ? 'its' : 'their'}{' '}
        rule affects. They are quarantined, not discarded — each is kept with the rule it broke.
        {flagged > 0 && (
          <span className="exclusion-aside">
            {' '}
            A further {flagged.toLocaleString()} {flagged === 1 ? 'is' : 'are'} flagged and still
            counted.
          </span>
        )}
      </span>
    </p>
  )
}
