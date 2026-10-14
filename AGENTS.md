# Project Instructions

## API and environment

- Store `PORTKEY_API_KEY` in the root `.env` file. Never hardcode, print, commit, or expose API keys.
- Load environment variables from the root `.env` file before making API requests.
- Route all AI calls through OpenAI using the model `gpt-6-luna`.

## Web app design

- Build any Labubu or other creature used in a web app directly in HTML/CSS/JavaScript; do not depend on raster image assets for the creature.
- Use a black-and-pink visual style for every web app: black or near-black backgrounds with pink as the primary accent color.
  - Exception (approved 2026-10-07 for the Campus Customs site in `hw 4/`): use a dark Yale-blue style, with near-black navy backgrounds, Yale Blue (#00356B) as the primary color and lighter blue highlights. Pink is used only for small accents such as low-stock notes.
  - Exception (approved 2026-10-14 for the Campus Customs operations work in `hw 5/`: the React dashboard in `hw 5/frontend/` and the agent monitor page): use the same Campus Customs colours as `hw 4/` (dark Yale blue, pink only for small accents).
