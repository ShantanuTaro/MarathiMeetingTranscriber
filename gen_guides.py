"""Writes static/help-*.html from the content below.

The two guides share a head, a nav and a footer and differ in everything else, so
the chrome lives here once and the pages ship as plain static HTML. Edit the data
at the bottom, run `python gen_guides.py`, commit the output.
"""
import html, json, os

OUT = "/Users/shantanu/Documents/marathi-transcribe/static"

NAV = """<header class="nav">
  <div class="wrap">
    <a class="brand" href="/"><span class="mark">म</span> Marathi Meeting Transcriber</a>
    <nav>
      <a href="/help/sarvam-api-key">Sarvam key</a>
      <a href="/help/elevenlabs-api-key">ElevenLabs key</a>
      <a href="/">Back to the app</a>
    </nav>
  </div>
</header>"""

FOOT = """<footer>
  <div class="wrap">
    <div class="foot-cols">
      <div>
        <strong>Marathi Meeting Transcriber</strong>
        <p>Marathi meeting audio to a reviewable Word document.</p>
      </div>
      <div>
        <span class="label">Guides</span>
        <a href="/help/sarvam-api-key">Get a Sarvam API key</a>
        <a href="/help/elevenlabs-api-key">Get an ElevenLabs API key</a>
      </div>
      <div>
        <span class="label">Tool</span>
        <a href="/">Transcribe a recording</a>
        <a href="/#how">How it works</a>
        <a href="/terms">Terms &amp; disclaimer</a>
      </div>
    </div>
    <div class="foot-base">
      <span>Your recording is uploaded to the service you pick.</span>
      <span>A draft for review, not a verbatim record.</span>
    </div>
  </div>
</footer>"""

ICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='.7' y2='1'>"
        "<stop offset='0' stop-color='%2337d7fa'/><stop offset='.4' stop-color='%234b72fe'/>"
        "<stop offset='.68' stop-color='%23ff8df2'/><stop offset='1' stop-color='%23ff8705'/>"
        "</linearGradient></defs><rect width='100' height='100' rx='22' fill='url(%23g)'/>"
        "<text x='50' y='72' text-anchor='middle' font-family='system-ui' font-size='58' "
        "font-weight='700' fill='%23fff'>म</text></svg>")


def page(g):
    steps_html = "\n".join(
        f"""      <li class="gstep">
        <h3>{s['h']}</h3>
        {s['body']}
      </li>""" for s in g["steps"])

    faq_html = "\n".join(
        f"""    <details>
      <summary>{q}</summary>
      <p>{a}</p>
    </details>""" for q, a in g["faq"])

    facts_html = "\n".join(
        f"""        <div class="fact"><dt>{k}</dt><dd>{v}</dd></div>"""
        for k, v in g["facts"])

    watch_html = "\n".join(f"      <li>{w}</li>" for w in g["watch"])

    # HowTo and FAQPage, the two schemas Google actually renders for a guide like this.
    # No canonical or og:url: this ships to run on localhost, and a canonical pointing
    # at a domain it is not served from is worse than none at all.
    ld = [
        {"@context": "https://schema.org", "@type": "HowTo", "name": g["title"],
         "description": g["desc"], "totalTime": g["mins"],
         "step": [{"@type": "HowToStep", "position": i + 1,
                   "name": s["h"], "text": s["plain"]}
                  for i, s in enumerate(g["steps"])]},
        {"@context": "https://schema.org", "@type": "FAQPage",
         "mainEntity": [{"@type": "Question", "name": q,
                         "acceptedAnswer": {"@type": "Answer", "text": a}}
                        for q, a in g["faq"]]},
    ]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <!--canonical-->
  <title>{g['title']}</title>
  <meta name="description" content="{g['desc']}" />
  <meta name="robots" content="index,follow" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="{g['title']}" />
  <meta property="og:description" content="{g['desc']}" />
  <link rel="stylesheet" href="/static/fonts.css" />
  <link rel="stylesheet" href="/static/style.css" />
  <link rel="stylesheet" href="/static/portal.css" />
  <link rel="icon" href="{ICON}" />
  <script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
</head>
<body>

{NAV}

<div class="wrap">
  <article class="guide">
    <p class="crumb"><a href="/">Marathi Meeting Transcriber</a> &rsaquo; API key guides</p>
    <h1>{g['h1']}</h1>
    <p class="lede">{g['lede']}</p>

    <dl class="facts">
{facts_html}
    </dl>

    <h2 id="steps">{g['steps_h']}</h2>
    <ol class="gsteps">
{steps_html}
    </ol>

    <h2 id="use-it">Use the key</h2>
    <p>{g['use_lede']}</p>
    <p class="snippet-label">In this app, no shell required</p>
    <pre class="snippet"><code>{html.escape(g['snip_app'])}</code></pre>
    <p class="snippet-label">From the command line</p>
    <pre class="snippet"><code>{html.escape(g['snip_sh'])}</code></pre>
    <p class="snippet-label">Straight against the API</p>
    <pre class="snippet"><code>{html.escape(g['snip_curl'])}</code></pre>

    <h2 id="watch-out">{g['watch_h']}</h2>
    <ul class="watch">
{watch_html}
    </ul>

    <h2 id="faq">Questions people actually ask</h2>
  </article>
</div>

<section class="band faq guide-faq">
  <div class="wrap">
{faq_html}
  </div>
</section>

<div class="wrap">
  <div class="guide-cta">
    <div>
      <strong>Got the key?</strong>
      <p>Open the app, pick {g['name']}, paste it in, and drop a recording.</p>
    </div>
    <a class="button" href="/">Transcribe a recording</a>
  </div>
  <p class="guide-note">{g['note']}</p>
</div>

{FOOT}

</body>
</html>
"""


SARVAM = {
    "slug": "help-sarvam-api-key",
    "name": "Sarvam",
    "title": "How to get a Sarvam AI API key (2026 step-by-step guide)",
    "desc": ("Create a Sarvam AI account, generate an api-subscription-key from the "
             "dashboard, and use it for Marathi speech-to-text. Steps, pricing, free "
             "credits and the mistakes that cost money."),
    "h1": "How to get a Sarvam AI API key",
    "mins": "PT5M",
    "lede": ("Sarvam is an India-first model provider, and <code>saaras:v4</code> is the "
             "best Marathi transcription this project has measured. Getting a key takes "
             "about five minutes and costs nothing: a new account comes with free credit "
             "and no card is asked for. Here is the whole path, plus the three things "
             "that quietly spend that credit with nothing to show for it."),
    "facts": [
        ("Dashboard", '<a href="https://dashboard.sarvam.ai" rel="noopener" target="_blank">dashboard.sarvam.ai</a>'),
        ("Docs", '<a href="https://docs.sarvam.ai/api-reference-docs/authentication" rel="noopener" target="_blank">docs.sarvam.ai</a>'),
        ("Auth header", "<code>api-subscription-key</code>"),
        ("Free credit", "&#8377;100 on signup, no expiry"),
        ("Speech-to-text", "&#8377;30 per hour of audio, &#8377;45 with speaker labels"),
        ("Card required", "No"),
    ],
    "steps_h": "Five steps to a working key",
    "steps": [
        {"h": "Create a Sarvam account",
         "plain": "Go to dashboard.sarvam.ai and sign up with email or Google. Verification is normally instant and no payment card is requested.",
         "body": ('<p>Go to <a href="https://dashboard.sarvam.ai" rel="noopener" target="_blank">dashboard.sarvam.ai</a> '
                  'and sign up with an email address or a Google account. Verification is '
                  'normally instant. <strong>No payment card is asked for</strong>, which is '
                  'the point of starting here rather than on a plan.</p>')},
        {"h": "Open the API Keys section",
         "plain": "In the dashboard sidebar, open API Keys. Rate limits are per account, not per key, so extra keys are for separating projects, not for more throughput.",
         "body": ('<p>In the dashboard, find <strong>API Keys</strong> in the sidebar. Sarvam '
                  'moves its dashboard around occasionally, so if the sidebar looks different, '
                  'look for the words rather than the position.</p>'
                  '<p>Worth knowing before you make several: <em>rate limits are counted per '
                  'account, not per key</em>. Extra keys separate projects and let you revoke '
                  'one without killing the others. They do not buy you more throughput.</p>')},
        {"h": "Create the key and copy it immediately",
         "plain": "Click to create a new key and copy it at once. The dashboard shows the full key only at creation time; afterwards it cannot be retrieved, only deleted and replaced.",
         "body": ('<p>Create a new key. <strong>Copy it before you navigate away.</strong> The '
                  'dashboard shows the full value exactly once, at creation. There is no '
                  '&ldquo;show again&rdquo;: if you lose it, the only route is to delete that '
                  'key and generate another.</p>'
                  '<p>Paste it somewhere safe for a moment. Not into a git repository, not into '
                  'a chat window, and not into a file you are about to commit.</p>')},
        {"h": "Check what you have to spend",
         "plain": "A new account gets Rs100 of credit that never expires. At Rs45 per hour with speaker labels on, that is about two hours and thirteen minutes of diarized audio.",
         "body": ('<p>A new account carries <strong>&#8377;100 of credit</strong>, universal '
                  'across Sarvam&rsquo;s APIs, with no expiry. Speech-to-text is &#8377;30 per '
                  'hour of audio, or <strong>&#8377;45 per hour with speaker labels on</strong>, '
                  'billed per second.</p>'
                  '<p>So the free grant is roughly <strong>2h13m of diarized audio</strong>: two '
                  'hour-long meetings, and not a minute more. That number is worth holding in '
                  'your head before you upload anything, because it goes faster than it sounds.</p>')},
        {"h": "Paste it into the app",
         "plain": "Open the transcriber, select Sarvam under Transcribed by, paste the key into the form and press Start session. The key is verified before any audio moves and is held in server memory only.",
         "body": ('<p>Open <a href="/">the transcriber</a>, pick <strong>Sarvam</strong> under '
                  '<em>Transcribed by</em>, and the key form opens by itself. Paste the key and '
                  'press <strong>Start session</strong>.</p>'
                  '<p>The key is checked against Sarvam before any audio moves, by creating a job '
                  'and never starting it. Sarvam bills nothing until a job starts, so verifying '
                  'costs zero and a bad key fails immediately rather than after an hour-long '
                  'upload. It then lives in the server&rsquo;s memory for the session only: never '
                  'written to disk, never sent back to the page, never included in any status '
                  'response. <em>End session</em> erases it, and so does restarting the server.</p>'
                  '<p>Optionally type what your dashboard says your credit balance is. There is no '
                  'API to read it, so the meter can only subtract from a number you supply.</p>')},
    ],
    "use_lede": ("The header name is <code>api-subscription-key</code>. Not "
                 "<code>Authorization</code>, not <code>Bearer</code>: a wrong header name "
                 "returns 401 and reads exactly like a wrong key."),
    "snip_app": ("Transcribed by  ->  Sarvam  ->  paste key  ->  Start session\n"
                 "then drop a recording. Nothing is installed and nothing is stored."),
    "snip_sh": ("export SARVAM_API_KEY=your_key_here\n"
                "export SARVAM_DIARIZE=0          # single voice? Rs30/hr instead of Rs45\n"
                ".venv/bin/python transcribe.py meeting.m4a"),
    "snip_curl": ('curl -X POST https://api.sarvam.ai/speech-to-text/job/v1 \\\n'
                  '  -H "api-subscription-key: $SARVAM_API_KEY" \\\n'
                  '  -H "Content-Type: application/json" \\\n'
                  '  -d \'{"job_parameters": {"model": "saaras:v4",\n'
                  '                          "with_diarization": true,\n'
                  '                          "with_timestamps": true}}\'\n\n'
                  '# Creating a job is free. Billing starts at /start, not here.'),
    "watch_h": "What quietly spends the credit",
    "watch": [
        ('<strong>Billing starts when the job starts, not when it succeeds.</strong> A run '
         'that comes back empty, errors, or gets stopped is billed in full for the whole '
         'duration of the audio. There is no partial charge and no refund.'),
        ('<strong>There is no cancel endpoint.</strong> Once a job has started, it runs to '
         'completion on Sarvam&rsquo;s side and bills, whatever your end does. Pressing Stop '
         'only stops the polling.'),
        ('<strong>A wrong Content-Type returns an empty transcript, billed in full.</strong> '
         'Sarvam decodes the uploaded blob by its declared mime type. Declare the wrong one '
         'and you get a blank transcript with the job marked <em>Success</em>. This app reads '
         'the real container with ffprobe rather than trusting the file extension, because '
         'the first real recording it ever handled was raw AAC named <code>.mp3</code>.'),
        ('<strong>Speaker labels are a price tier, not a switch.</strong> Diarization takes '
         'the rate from &#8377;30 to &#8377;45 an hour, a 50% premium. On a single-voice '
         'recording it buys nothing. <code>SARVAM_DIARIZE=0</code>, or the equivalent in your '
         'own code, is a third off the bill.'),
    ],
    "faq": [
        ("Do I need a credit card to get a Sarvam API key?",
         "No. Signing up at dashboard.sarvam.ai takes an email address or a Google account, "
         "and a new account is credited with Rs100 automatically. A card is only needed when "
         "you want to add credit beyond that."),
        ("How much Marathi audio does the free credit cover?",
         "About 2 hours 13 minutes with speaker labels on, at Rs45 per hour of audio. With "
         "diarization off it is Rs30 per hour, so roughly 3 hours 20 minutes. Billing is per "
         "second of audio, not per request."),
        ("I lost my API key. Can I see it again?",
         "No. The dashboard shows the full key only at the moment of creation. Delete that key "
         "and create a new one; anything using the old key will stop working, which is exactly "
         "what you want if you lost it because it leaked."),
        ("What is the header name for Sarvam authentication?",
         "api-subscription-key. It is not an Authorization header and there is no Bearer prefix. "
         "Sending the right key under the wrong header name returns 401 and looks identical to "
         "sending a bad key."),
        ("Is my key safe in this app?",
         "It is held in the server's memory for the length of the session and nowhere else: never "
         "written to disk, never sent back to the browser, never included in any job or status "
         "response, and never handed to a different provider. Ending the session or restarting "
         "the server erases it. Closing the tab does not, so use the End session button."),
        ("Why is my transcript empty even though the job succeeded?",
         "Almost always a Content-Type that does not match the actual audio container. Sarvam "
         "decodes by the declared type, produces nothing usable, and still marks the job "
         "successful and bills it. Check the real format with ffprobe rather than trusting the "
         "file extension."),
        ("Can I send a recording longer than two hours?",
         "Not in one job: Sarvam caps a single file at 2 hours. Split it into parts and stitch "
         "the timestamps back together. This app does that automatically, cutting into equal "
         "parts and re-encoding them so a 269 MB source uploads as two 34 MB parts."),
    ],
    "note": ("Prices, free-credit amounts and dashboard layouts are Sarvam&rsquo;s to change. "
             "Everything here was checked against docs.sarvam.ai in September 2026; treat the "
             "official docs as the authority if the two ever disagree."),
}

ELEVEN = {
    "slug": "help-elevenlabs-api-key",
    "name": "ElevenLabs Scribe",
    "title": "How to get an ElevenLabs API key for Scribe speech-to-text (2026 guide)",
    "desc": ("Create an ElevenLabs API key, enable the Speech to Text permission it needs, "
             "and use it to transcribe Marathi with Scribe v2. Steps, the restricted-key "
             "trap, and per-word confidence."),
    "h1": "How to get an ElevenLabs API key",
    "mins": "PT5M",
    "lede": ("ElevenLabs Scribe is the only backend this project supports that returns a "
             "<strong>per-word confidence score</strong>, which is what lets a document "
             "highlight the words the model was unsure of instead of asking you to re-read "
             "the whole hour. Getting a key takes a few minutes, and there is exactly one "
             "step people miss."),
    "facts": [
        ("Where", '<a href="https://elevenlabs.io/app/settings/api-keys" rel="noopener" target="_blank">Developers &rsaquo; API Keys</a>'),
        ("Docs", '<a href="https://elevenlabs.io/docs/capabilities/speech-to-text" rel="noopener" target="_blank">elevenlabs.io/docs</a>'),
        ("Auth header", "<code>xi-api-key</code>"),
        ("Model", "<code>scribe_v2</code>"),
        ("Per file", "up to 3 GB, up to 10 hours"),
        ("Marathi", "rated high accuracy (5&ndash;10% WER)"),
    ],
    "steps_h": "Six steps, one of which bites",
    "steps": [
        {"h": "Sign in to ElevenLabs",
         "plain": "Sign in or register at elevenlabs.io. A free tier exists; Scribe usage is billed against your plan's credit quota.",
         "body": ('<p>Sign in or register at <a href="https://elevenlabs.io/app/sign-in" '
                  'rel="noopener" target="_blank">elevenlabs.io</a>. There is a free tier. '
                  'Speech-to-text is billed against your plan&rsquo;s credit quota rather than '
                  'a per-hour rupee rate, which is why this app&rsquo;s spend meter counts jobs '
                  'for ElevenLabs instead of inventing a currency figure.</p>')},
        {"h": "Open Developers, then API Keys",
         "plain": "Click Developers in the left sidebar and choose the API Keys tab. It can also be reached from My Workspace in the lower left.",
         "body": ('<p>Click <strong>Developers</strong> in the left sidebar, then the '
                  '<strong>API Keys</strong> tab. The same page is reachable from '
                  '<strong>My Workspace</strong> in the lower left corner, and directly at '
                  '<a href="https://elevenlabs.io/app/settings/api-keys" rel="noopener" '
                  'target="_blank">elevenlabs.io/app/settings/api-keys</a>.</p>')},
        {"h": "Create a key and give it a name that means something",
         "plain": "Press Create Key and name it for where it will be used, so that revoking the right one later is obvious.",
         "body": ('<p>Press <strong>+ Create Key</strong>. Name it for where it is going to live: '
                  '<code>marathi-transcriber-laptop</code> beats <code>key 3</code> on '
                  'the day you need to revoke exactly one of them.</p>')},
        {"h": "Enable Speech to Text on the key",
         "plain": "This is the step people miss. New keys are restricted by default and grant no product access until you tick it. Enable Speech to Text or every transcription request fails with 401.",
         "body": ('<p><strong>This is the step people miss.</strong> An ElevenLabs key is '
                  '<em>restricted by default</em>: it grants access to nothing until you tick the '
                  'products it may use. Find <strong>Speech to Text</strong> in the permission '
                  'list and enable it.</p>'
                  '<p>Skip this and the key is real, valid, and completely useless for '
                  'transcription. Every request comes back 401, which reads exactly like a typo '
                  'in the key, and you will spend twenty minutes re-copying a key that was never '
                  'the problem.</p>')},
        {"h": "Set a credit limit while you are there",
         "plain": "Optionally cap how many credits this key may spend. A cap turns a runaway loop into a failed request instead of an invoice.",
         "body": ('<p>Optional, and worth the ten seconds: each key can carry its own credit cap. '
                  'A capped key turns a runaway loop into a failed request rather than an '
                  'invoice. If the key is going anywhere near a script you are still writing, '
                  'cap it.</p>')},
        {"h": "Copy the key, then paste it into the app",
         "plain": "The full key is shown only at creation; afterwards only the last four characters are visible. Copy it, then paste it into the transcriber and press Start session.",
         "body": ('<p>The full value is shown <strong>only at creation</strong>. Afterwards the '
                  'list shows the name and the last four characters and nothing else.</p>'
                  '<p>Open <a href="/">the transcriber</a>, pick <strong>ElevenLabs Scribe</strong> '
                  'under <em>Transcribed by</em>, paste, and press <strong>Start session</strong>. '
                  'The key is verified against your account before any audio moves, then held in '
                  'the server&rsquo;s memory for the session only. One session is one provider, so '
                  'an ElevenLabs key can never be sent to Sarvam.</p>')},
    ],
    "use_lede": ("The header name is <code>xi-api-key</code>. Like Sarvam&rsquo;s, it is not an "
                 "<code>Authorization</code> header and takes no <code>Bearer</code> prefix."),
    "snip_app": ("Transcribed by  ->  ElevenLabs Scribe  ->  paste key  ->  Start session\n"
                 "then drop a recording. Nothing is installed and nothing is stored."),
    "snip_sh": ("export ELEVENLABS_API_KEY=your_key_here\n"
                "export SCRIBE_LANG=mar           # say Marathi; auto-detect can pick English\n"
                "ASR=elevenlabs .venv/bin/python transcribe.py meeting.m4a"),
    "snip_curl": ('curl -X POST https://api.elevenlabs.io/v1/speech-to-text \\\n'
                  '  -H "xi-api-key: $ELEVENLABS_API_KEY" \\\n'
                  '  -F model_id=scribe_v2 \\\n'
                  '  -F language_code=mar \\\n'
                  '  -F diarize=true \\\n'
                  '  -F timestamps_granularity=word \\\n'
                  '  -F file=@meeting.m4a\n\n'
                  '# 401 here with a key you just copied? The key is probably\n'
                  '# missing the Speech to Text permission. See step 4.'),
    "watch_h": "Before you upload",
    "watch": [
        ('<strong>A restricted key looks exactly like a wrong key.</strong> If a freshly '
         'created key returns 401 on a transcription request, it is almost certainly missing '
         'the <em>Speech to Text</em> permission rather than mistyped.'),
        ('<strong>Set the language explicitly.</strong> On code-switched Marathi and English '
         'audio, auto-detect can settle on English and hand back a translated transcript '
         'instead of a Marathi one. <code>language_code=mar</code> costs nothing and removes '
         'the coin flip.'),
        ('<strong>Keyword biasing bills 20% extra.</strong> Scribe&rsquo;s <code>keyterms</code> '
         'field is genuinely useful for village names and scheme acronyms, but the surcharge '
         'applies whenever the field is present, whether or not any term is ever matched.'),
        ('<strong>Re-tune your confidence threshold.</strong> Scribe returns a logprob, and '
         '<code>exp(logprob)</code> is a different distribution from Whisper&rsquo;s '
         'probabilities. Carry a threshold over unchanged and you get either every word '
         'flagged or none of them. Measure it on one real meeting.'),
        ('<strong>One request covers upload and transcription.</strong> There is no progress to '
         'poll for, so a long file simply takes as long as it takes. This app draws an '
         'estimated bar from elapsed time rather than pretending to know.'),
    ],
    "faq": [
        ("Why does my new ElevenLabs API key return 401?",
         "Because new keys are restricted by default and grant no product access until you enable "
         "it. Open the key in Developers > API Keys and tick Speech to Text. A key without that "
         "permission is valid but rejects every transcription request, which is indistinguishable "
         "from a wrong key."),
        ("What is the header name for ElevenLabs authentication?",
         "xi-api-key. It is not an Authorization header and there is no Bearer prefix."),
        ("Can I see an ElevenLabs API key again after creating it?",
         "No. The full value appears only at creation. Afterwards the dashboard shows the key's "
         "name and its last four characters. If you lose it, create a new key and delete the old one."),
        ("How long a recording can Scribe take?",
         "Up to 3 GB and up to 10 hours per request in standard mode, which is far beyond any "
         "normal meeting. Sarvam, by contrast, caps a single file at 2 hours."),
        ("How good is Scribe at Marathi?",
         "ElevenLabs rates Marathi in its high-accuracy band, between 5% and 10% word error rate. "
         "For code-switched meeting audio it is a reasonable choice, and it is the only option "
         "here that reports per-word confidence, so uncertain words can be highlighted for review."),
        ("Does Scribe label speakers?",
         "Yes, up to 32 speakers with diarization enabled, and unlike Sarvam it does not charge a "
         "different rate for it."),
        ("Can I limit what a key is allowed to spend?",
         "Yes. Each key can carry its own credit cap, set when you create it or later by editing "
         "it. It is the cheapest insurance there is against a loop you did not mean to write."),
    ],
    "note": ("Permissions, limits and dashboard layouts are ElevenLabs&rsquo; to change. "
             "Everything here was checked against elevenlabs.io/docs in September 2026; treat "
             "the official docs as the authority if the two ever disagree."),
}

for g in (SARVAM, ELEVEN):
    path = os.path.join(OUT, g["slug"] + ".html")
    open(path, "w").write(page(g))
    print("wrote", path)
