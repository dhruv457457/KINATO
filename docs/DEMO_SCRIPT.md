# Kinato — demo video voiceover

Narration for the submission video, written in the order the clips were shot.
One idea per line. Breathe at the line breaks, not at the full stops.

**~4:10 of narration, ~640 words**, before the recorded call clips.
Lines marked **bold** are the ones to land — slow down slightly and leave a gap after.

---

## 1 — Landing page · 0:35

*On screen: scrolling the landing page*

Hi, I'm Dhruv Pancholi, from Jaipur.

I built Kinato for the Razorpay AI Buildathon.

*— beat —*

Here is the problem.

When a customer's payment fails on your store, that sale is usually just gone.

Nobody follows up. An email sits there unopened.

**Kinato picks up the phone instead.**

It calls the customer, asks what went wrong, and sends them a new payment link.

And this is a real AI agent. It listens, it works out the actual reason, and it decides what to say next.

It is a conversation, not a recording.

But there is one thing it is not allowed to do.

**It cannot decide the money. I will show you why that matters.**

---

## 2 — Sign up and onboarding · 0:50

*On screen: login → create account → Razorpay → integration → catalogue → policy*

Let me create a new account.

You can sign up with Google, or with an email.

Then setup is four steps.

*— slow down here —*

First, connect Razorpay. You paste your test keys, and now Kinato can create real payment links for you.

Second, integration. One line of code on your website. That is all.

If your store already uses Razorpay Checkout, Kinato picks up the cart by itself.

Third, your catalogue.

And this is not a fixed template. This is the messy file you already have — wrong column names, extra rows on top, prices written in three different ways.

It reads all of it, and shows you what it understood before saving anything.

It asks you to confirm one thing. Your cost price.

Because that is what decides how much discount is even allowed.

And fourth, your policy. How much you are willing to give, and how low you will go.

That is the whole setup.

---

## 3 — Dashboard · 0:20

*On screen: logging into your existing account → dashboard totals*

Now let me go into the account I have actually been using.

This is the dashboard.

**And this is money that Kinato has genuinely brought back.**

Real payment links, real customers.

Every rupee here came from a phone call.

---

## 4 — Ask · 0:18

*On screen: the Ask tab*

This is Ask.

You can just talk to your own business.

Why are people dropping off. How much came back this week. Which product customers hesitate on.

And it answers from your real records. Not a general guess.

---

## 5 — Recoveries, the three calls · 0:55

*On screen: open each of the three recoveries in turn*

This is where every call is recorded. What was said, and what was done.

Let me open three different ones.

*— first one —*

This customer paid full price. Their card had simply failed. They did not need a discount, they needed a working link.

*— second one —*

This customer asked for a discount. Now watch what happens.

It starts at three percent. The customer pushes back. It goes to seven.

Then they ask for forty percent, and it says no.

**That refusal is not the AI being polite. That is the code stopping it.**

*— third one —*

And this one is my favourite.

The customer said they will pay later, on a particular day.

So the agent stopped selling. It did not push a discount. It took the date and stepped back.

Because money off does not fix a salary that has not arrived yet.

---

## 6 — Catalogue, customers, policy · 0:35

*On screen: catalogue tab → customers tab → policy page*

Here is your catalogue, and here are your customers.

And this is the policy page. This is where the shop owner is in charge.

You pick the language. English, Hindi, Telugu, or Hinglish.

You write how the agent should talk, in your own words.

You can switch on EMI, so instead of cutting the price it offers instalments.

And you choose the hours.

**Nobody gets a call at eleven at night.**

---

## 7 — Settings · 0:18

*On screen: settings tab*

In settings you set your business name.

That is the name the customer actually hears on the call.

You get your API keys here.

And you add your website address, so only your own store can send data in.

---

## 8 — The three diagrams · 0:55

*On screen: README — sequence diagram, then the pie chart, then the architecture diagram*

Let me quickly show you how this is built.

*— sequence diagram —*

This first one is the important one.

When the agent wants to give a discount, it does not decide it. It asks.

The code checks your policy, and hands back a token — and the real amount is already fixed inside that token.

The agent can only pass that token back. It never touches the number.

**So even if the AI gets confused, or somebody tries to trick it, the price always comes from your policy.**

*— pie chart —*

This is twenty-five test carts.

And look at this part. Ten of them were never called at all.

Some had not given permission. Some had already paid. For one it was too late at night.

**Not calling is also a feature.**

*— architecture diagram —*

And this last one is the full picture.

Green is ordinary code. Blue is the AI.

And nothing blue ever touches the money.

**The AI does the talking. The code does the money.**

---

## 9 — Close · 0:20

*On screen: you, or a plain end card*

Two honest things before I finish.

Twilio is on a free trial, so it can only call numbers I have verified. That is why every call you saw came to my own phone.

And Razorpay test mode only allows thirty payment links.

Everything else you saw is real.

*— beat —*

**That is Kinato. Thank you for watching.**

> Say the limits calmly, not apologetically. Naming them before anyone asks reads as
> confidence. Being caught out on them later does not.

---

## If the video runs long

Cut in this order — each loses the least:

1. Scene 7, settings
2. Scene 4, Ask
3. The catalogue half of scene 6

**Never cut scene 5 or scene 8.** Those two are the only parts a judge cannot get
from anybody else's submission.

## Reading it well

- **Breathe at the line breaks, not the full stops.** Every line is one idea. Run two
  together and you start rushing, and it shows.
- **Record in one pass, mistakes and all.** Don't stop to fix. Say the line again and
  keep going — picking the good take later is far quicker than restarting.
- **Stand up.** It changes your voice, and it kills the flat reading-off-a-page sound.
- **If a sentence feels awkward in your mouth, change it.** Your own words in your own
  rhythm will always sound better than mine.
