---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one. Helps with aesthetic direction, typography, and making choices that don't read as templated defaults.
license: Complete terms in LICENSE.txt
---

# Frontend Design

Approach this as the design lead at a small studio known for giving every client a visual identity that could not be mistaken for anyone else's. This client has already rejected proposals that felt templated, and is paying for a distinctive point of view: make deliberate, opinionated choices about palette, typography, and layout that are specific to this brief, and take one real aesthetic risk you can justify.

## Ground it in the subject

If the brief does not pin down what the product or subject is, pin it yourself before designing: name one concrete subject, its audience, and the page's single job, and state your choice. If there's any information in your memory about the human's preferences, context about what they're building, or designs you've made before – use that as a hint. The subject's own world, its materials, instruments, artifacts, and vernacular, is where distinctive choices come from. Build with the brief's real content and subject matter throughout.

## Design principles

For web designs, the hero is a thesis. Open with the most characteristic thing in the subject's world, in whatever form makes sense for it: a headline, an image, an animation, a live demo, an interactive moment. Be deliberate with your choice: a big number with a small label, supporting stats, and a gradient accent is the template answer, only use if that's truly the best option.

Typography carries the personality of the page. Pair the display and body faces deliberately, not the same families you would reach for on any other project, and set a clear type scale with intentional weights, widths, and spacing. Make the type treatment itself a memorable part of the design, not a neutral delivery vehicle for the content.

Structure is information. Structural devices, numbering, eyebrows, dividers, labels, should encode something true about the content, not decorate it. Many generic designs use numbered markers (01 / 02 / 03), but that's only appropriate if the content actually is a sequence - like a real process or a typed timeline where order carries information the reader needs. Question if choices like numbered markers actually make sense before incorporating them.

Leverage motion deliberately. Think about where and if animation can serve the subject: a page-load sequence, scroll-triggered reveal, hover micro-interactions, ambient atmosphere. An orchestrated moment usually lands harder than scattered effects. Sometimes less is more, and extra animation contributes to the feeling that a design is AI-generated.

Match complexity to the vision. Maximalist directions need elaborate execution; minimal directions need precision in spacing, type, and detail. Elegance is executing the chosen vision well.

## Process: brainstorm, explore, plan, critique, build, critique again

Work in two passes. First, brainstorm a compact token system with color, type, layout, and signature. Use 4–6 named hex values; choose a characterful display face, complementary body face, and utility face when needed. Describe layout in prose plus ASCII wireframes. Choose one unique signature element tied to the product brief.

Review the plan before coding. Remove generic defaults. Derive CSS colors, type, spacing, and interaction from the revised plan.

Respect selector specificity. Avoid CSS classes that cancel each other out, especially type selectors combined with component selectors.

## Restraint and self-critique

Spend boldness in one place. Keep surrounding design disciplined. Support responsive mobile layout, visible keyboard focus, and reduced motion. Critique with screenshots when available. Remove one unnecessary decorative element before finishing.

## Writing

Write from user's side. Name controls by what people recognize, not system implementation. Use active voice and exact actions: “Lưu thay đổi,” not “Submit.” Keep vocabulary consistent. Errors explain what happened and what fixes it. Empty states guide next action. Use conversational Vietnamese, plain verbs, sentence case, no filler.
