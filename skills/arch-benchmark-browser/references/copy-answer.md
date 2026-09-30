# Copy the answer

Step 7 reads this. A site's copy button gives the answer as the
product's own Markdown: its headings, lists, and code as the product
wrote them. The page text holds only what the page draws.

1. Scroll the conversation until the end of the product's answer is in
   view. Its copy button is the icon of two overlapping squares in the
   row of small buttons directly under the answer, beside thumbs up,
   thumbs down, or retry. `find` it by its name ("Copy", "Copy
   response"), or pick it from a screenshot and a `zoom`. A copy button
   by the sent prompt, above the answer, copies the prompt: never click
   that one. Where the answer ran through tool steps, the button under
   its last part copies the whole response.
2. Click it once, then read the clipboard with `pbpaste` (macOS).
3. Check that the paste is this answer. It is not the prompt, and it is
   not the paste an earlier site gave in this run. Where the answer
   gave a score, it holds the contract's `Score: NN/100` line with the
   score the page shows. Where it gave none, it starts with the words
   the answer starts with on the page. The page is read for this check
   with `find`, or with a screenshot and a `zoom` where `find` is
   refused. A paste that fails the check is
   never saved or quoted: it may be something else the person copied.
   Click the same button once more and check again.
4. Save the paste as the `## Answer` section exactly as it came, with
   no line added, dropped, or rewrapped.

The fallback: where the answer has no copy button, `pbpaste` fails, or
the second paste fails the check, read the answer with `get_page_text`,
or from screenshots where that is refused, and bound it as "What the
pages are like" says. The session's `note` then says the answer is page
text, and why.
