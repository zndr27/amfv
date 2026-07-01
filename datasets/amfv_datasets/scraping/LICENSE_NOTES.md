# Source licensing notes for scraped corpora

## WHO (World Health Organization)

Publications on [who.int](https://www.who.int/publications) published since
November 2016 are licensed under **Creative Commons Attribution-NonCommercial-
ShareAlike 3.0 IGO** (CC BY-NC-SA 3.0 IGO).

- **Non-commercial use and adaptation** are permitted.
- **Attribution** to WHO is required.
- **Share-alike**: derivatives must use the same or a similar licence.

Pre-2017 publications were not reissued under this licence. Use
`metadata.publication_date` to filter when building corpora.

### Suggested attribution

> © World Health Organization {year}. *{publication title}*.
> Licensed under CC BY-NC-SA 3.0 IGO.
> https://creativecommons.org/licenses/by-nc-sa/3.0/igo/

Each scraped document also records `metadata.license` and
`metadata.attribution`.

### Milestone 1 content scope

The WHO scraper collects the HTML **Overview** section from each publication
landing page (`metadata.content_scope = "overview"`). Full guideline text is
typically available only as a linked PDF (`metadata.download_url`). PDF
extraction is intentionally deferred.
