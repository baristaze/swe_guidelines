# Tell when grok.com is done

Step 6 reads this at every poll of grok.com.

The text is the same when the answer's length and its own last line
match the previous poll's; the chrome after the answer is ignored. When
they match, check the button beside the composer with `find` or
`read_page`, or with a `zoom` where both are refused. "Enter voice
mode" confirms the answer is done. A stop control means it is not,
whatever the text did: keep polling. A tab that was not in front for
its waits is never called done.
