# Write the site file

Step 7 reads this, once per site, to save `<site>.md` in the run
folder:

```text
# <site>

- URL: <url>
- Model: <model_label>
- Effort: <effort_label>
- Sent: <started_at>
- Finished: <finished_at> (how it was read)
- Status: <status>
- Score: <NN>/100
- Read: <read_version>
- Polls: <polls>
- Note: <note>

## Answer

<the answer>
```

Where `score` is null, the header's line is `- Score: none`; where
there is no note, it is `- Note: none`. A copied answer is saved as it
came. A page-text answer is bounded as the skill's "What the pages are
like" says, and the chrome on either side is left out; tool steps,
citation chips ("GitHub", "10 sources"), and image captions stay as the
page gave them.
