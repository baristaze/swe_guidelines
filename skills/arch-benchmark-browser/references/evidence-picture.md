# Take the evidence picture

Step 9 reads this, once per site that sent the prompt, for the line
step 9 names.

1. Click an empty margin to clear a stray selection or menu, `find`
   the line, and `scroll_to` its reference. Where `find` is refused,
   scroll the conversation with the `computer` tool's `scroll` until a
   screenshot shows the line.
2. Take one `zoom` of the conversation column with `save_to_disk`. Its
   region is right of the sidebar and below the page's header, where
   claude.ai writes the conversation's own title: it shows that line
   and the chip, and no conversation title.
3. Copy it into the run folder as `<site>` with the extension of the
   path the tool reports (`<site>.png` when it saves PNG), with
   `python3` (`shutil.copyfile` from that path).
