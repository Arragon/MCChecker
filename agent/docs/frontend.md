# Frontend, Interface, and Interaction Rules

## 1. Applicability

This file must be loaded when the task involves the following:

- Modifying pages, components, templates, or layouts
- Modifying CSS, themes, icons, or visual styles
- Modifying forms, modals, tables, navigation, or interaction behaviors
- Responsive adaptation, mobile adaptation, or accessibility improvements
- Modifying frontend states, loading processes, error feedback, or empty states

---

## 2. Pre-modification Check

Before starting, you must:

1. View existing pages, component structures, and style organization methods.
2. Prioritize checking and reusing:
   - Global styles under `/app/static/css`, if the directory exists
   - Existing design variables, buttons, forms, cards, tables, and modal styles
   - Existing layout components and responsive breakpoints
3. View interaction behaviors and copywriting styles of similar pages.
4. Confirm whether modification affects:
   - API calls
   - Frontend state management
   - Permission control
   - User operation instructions
   - Frontend tests or screenshot tests

---

## 3. Visual Consistency

- New interfaces must follow the project's existing design language.
- Distinctly inconsistent fonts, colors, spacing, or component styles should not be created for a single page.
- Prioritize reuse of existing class names, variables, components, and layout rules.
- If new common styles are indeed needed, they should be placed in appropriate existing global locations rather than repeatedly defined in a scattered manner.
- Copywriting should be concise, clear, and consistent with existing terminology.

---

## 4. Layout and Responsive

Interfaces must be usable at reasonable sizes:

- Prioritize use of Flexbox, Grid, fluid widths, and reasonable spacing systems.
- Set reasonable `max-width`, line breaks, and overflow handling for content areas.
- Avoid depending on unnecessary fixed widths or fixed heights.
- Avoid buttons, forms, tables, and modals being obscured or horizontally overflowing on small screens.
- Tables or long content on narrow screens should have readable adaptation strategies, such as scrolling, folding, or cardification.
- Modals should avoid exceeding the viewport and allow content scrolling.

---

## 5. State and Feedback

For user-facing asynchronous or failable operations, provide according to the scenario:

- Loading state
- Empty state
- Error state
- Disabled state
- Success or failure feedback
- Retry or recovery path

Requirements:

- Avoid repeated submissions during submission.
- Error messages should help users take the next step.
- Internal stacks, keys, request headers, or sensitive response content should not be displayed directly to users.

---

## 6. Forms and Interaction

- Inputs should have clear labels, prompts, and necessary validation.
- Required fields, format errors, and submission errors should have recognizable feedback.
- Dangerous operations, such as deletion, overwriting, and clearing, should provide clear confirmation or anti-misclick mechanisms.
- Keyboard operations, focus states, and accessible names should be as complete as possible.
- Errors or states should not be expressed only through color.

---

## 7. Frontend Security

Load `/agent/docs/security.md` simultaneously when involving the following scenarios:

- Displaying user-entered content
- Rich text, HTML rendering, or URL redirection
- Uploading or downloading files
- Login, permissions, or user identity information
- Token, local storage, or sensitive configuration processing

Frontend must not:

- Write server-side keys into static resources.
- Output sensitive information in the console.
- Replace real permission validation with hidden buttons.
- Render untrusted HTML directly without security processing.

---

## 8. Testing and Documentation

After frontend modifications are completed, execute according to project capabilities:

- Relevant component or page tests
- Frontend unit tests
- Build checks
- Type checks or lint
- Manual verification of key flows
- Basic layout checks under different sizes

If user operation methods, page entries, parameters, or feature highlights change, update:

- `/docs/manual.md`
- `/README.md` or `/readme.md`, if it affects project feature overview or quick start
