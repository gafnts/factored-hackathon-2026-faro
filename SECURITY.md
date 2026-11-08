# Security policy

Faro is a prototype: a card support agent for a synthetic bank, running at [faro.gabriel.com.gt](https://faro.gabriel.com.gt). One person maintains it. We welcome reports about its security and answer them as fast as one person can.

## Report a vulnerability

Report it privately, through GitHub: open the repository's **Security** tab and choose [**Report a vulnerability**](https://github.com/gafnts/factored-hackathon-2026-faro/security/advisories/new). Please don't open an issue, a pull request, or a discussion about it.

Tell us what you did, what happened, and what you expected, with the requests or messages that show it. For the agent, the conversation's text is enough. Don't send real personal or card data, yours or anyone's.

We reply within a week, and tell you what we decide to do. When a fix ships, the advisory credits you, unless you'd rather it didn't. There is no bounty.

## What's in scope

- The code, workflows, and infrastructure in this repository.
- The prototype at [faro.gabriel.com.gt](https://faro.gabriel.com.gt), which runs the latest release.

What we most want to hear about is anything that breaks what Faro promises:

- a customer reading another customer's cards, cases, or conversations;
- a card blocked, or any other action taken, without its holder's confirmation;
- a customer reaching the staff console, or getting a staff token;
- a way past the sign-in, the limits on turns, or the pause;
- a secret, or data that isn't synthetic, in the repository or on the site.

A message that makes the agent do one of these counts, prompt injection included. The [evaluation report](docs/evaluation/report.md) says what we measured against them.

## What's out of scope

- Wrong or odd answers from the agent that break none of the promises above. Open an [issue](https://github.com/gafnts/factored-hackathon-2026-faro/issues) for those.
- Load tests, denial of service, and using up the day's turns or guests. The limits exist for this, and a run against them stops the prototype for everyone.
- Social engineering, and attacks on our devices or accounts.
- Flaws in AWS's, Anthropic's, or GitHub's own services. Report those to the provider.
- Missing headers or settings with no impact you can show.
- Data in the bank's records. The bank, its customers, and their cards are synthetic.

## How to test

- Use only the access you're given.
- Stop at the first sign that you can reach someone else's data, and report what you saw instead of going further.
- Don't run automated scanners against the prototype. They spend the day's turns, which everyone shares.
- Don't type real personal or card data.

If you follow this policy in good faith, we won't take or support legal action against you for your research.

## Supported versions

Only the latest release, the one running at [faro.gabriel.com.gt](https://faro.gabriel.com.gt), gets fixes. A fork runs its own stack, which we can't patch; the advisory says what to change.
