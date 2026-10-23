# Scraper-methoden per website

Deze inventaris beschrijft de detectiemethoden die de scraper nu inzet. Het zijn geen exclusieve labels: meerdere methoden mogen per site samenwerken.

## Methodiek-categorieën

- **JSON-LD / JSON-site** — gestructureerde evenementgegevens in een ld+json-script; meestal betrouwbaar voor titel, datum, locatie en URL.
- **HTML-kaarten** — titel, datum en link uit zichtbare agenda-/programmacards.
- **HTML-detailpagina** — detailpagina uitlezen wanneer de agenda alleen een link bevat.
- **RSS/Atom** — feeds ontdekken en items als evenementen verwerken.
- **Datum-/tekstregels** — datum herkennen in gewone tekst of compacte labels.
- **Site-specifiek HTML** — regels voor afwijkende markup of datumnotatie.
- **Zoek-/AI-invoer** — optionele SerpAPI- of AI-kandidaten; apart te valideren tegen de bron.

## Websites en huidige aanpak

| Website | Categorie | Huidige strategie / kans op hergebruik |
|---|---|---|
| Groningen Uitloper | Site-specifiek HTML + datumregels | Eén pagina per dag; herbruikbaar voor agenda's die dag voor dag pagineren. |
| Visit Groningen evenementen | JSON-LD + generieke HTML-kaarten + feeds | Generieke methoden herbruikbaar voor veel CMS-sites. |
| Visit Groningen wandelen | HTML-kaarten + wandel-specifieke parser | De generieke kaartparser kan als fallback dienen. |
| Forum Groningen | JSON-LD + generieke HTML-kaarten + feeds + detailpagina's | Goede kandidaat voor hergebruik op andere JSON-LD/HTML-sites. |
| SPOT Groningen | Site-specifiek HTML-kaarten + feeds + detailpagina's | Venue-parser deelt code met Simplon; generieke parser kan aanvullend getest worden. |
| Simplon | Site-specifiek HTML-kaarten + compacte datumregels | Werkende gespecialiseerde parser behouden; datumstrip en kaartdetectie zijn site-specifiek. |
| VERA | Site-specifieke programmapagina + HTML-kaarten | Bewust niet de generieke nieuwsparser gebruiken om oude nieuwsitems als events te voorkomen. |
| Martiniplaza | Site-specifiek HTML + generieke HTML/JSON-LD + feeds + detailpagina's | Beide parsers worden nu gecombineerd; dit vergroot de kans dat verschillende kaartstructuren worden herkend. |
| Groninger Museum | Generieke HTML/JSON-LD + feeds | Controleer of de Engelstalige /en/now-pagina toekomstige evenementen bevat. |
| Kultuuragenda | Generieke HTML/JSON-LD + feeds | Herbruikbare standaardmethoden. |
| Noorderzon | Generieke HTML/JSON-LD + feeds | Seizoensgebonden; mogelijk tijdelijk weinig of geen komende events. |
| Concertgebouw | Generieke HTML/JSON-LD + feeds + detailpagina's | De standaardmethoden zijn herbruikbaar. |
| Hedon | Generieke HTML/JSON-LD + feeds + detailpagina's | De standaardmethoden zijn herbruikbaar. |
| Paradiso | Generieke HTML/JSON-LD + feeds + detailpagina's | De standaardmethoden zijn herbruikbaar. |
| VVV Ameland | Generieke HTML/JSON-LD + feeds | Controleer of de startpagina voldoende agenda-links bevat. |
| Social Deal / Groupon | Generieke HTML + site-specifieke aanbiedingen | Commerciële aanbiedingen kunnen afwijken van evenementen; apart valideren. |
| iBOOD | Site-specifieke HTML-aanbiedingen | De aanbiedingenparser is niet zomaar toepasbaar op agenda's. |

## Hoe een methode beoordelen

1. Controleer per site de ruwe verversdata: HTTP-status, uiteindelijke URL, HTML-lengte, aantallen per parser en fouten.
2. Test eerst JSON-LD, daarna generieke HTML-kaarten, feeds en detailpagina's.
3. Voeg pas site-specifieke regels toe wanneer de generieke aanpak onvoldoende resultaten geeft.
4. Vergelijk aantallen én titels/datums; een hoger aantal kan ook fout-positieven betekenen.
5. Laat een werkende site-specifieke parser (zoals Simplon en VERA) niet vervangen zonder regressietest.
