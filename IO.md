The high level summary is that:

The root level IO model is:

- Actors via effects.

- Low level IO via effects (standardized subset for networking, etc)

- Non-portable IO via effects.

And then slowly generalize this as capabilities expand, by:

1. finish the design of the optimizer, finalize the jet nouns, and implement virtualization.

2. Implement the cog/drone SSI system as a usage pattern over the base effects.  The primary advantage of this is more efficient persistence and a more robust error handling model.

Virtualization design is well explored but can't be finalized until the optimizer design standardizes, because concrete designs need to be evaluated w.r.t how well they optimize.

Similarly, actors and snapshots should be used in anger and stabilized before building cog/drone on top of it.
