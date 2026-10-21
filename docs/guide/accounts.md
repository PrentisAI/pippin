# Accounts and sharing

Out of the box the console is single-user: nobody signs in, every project is
yours, and model calls use the server's `OPENROUTER_API_KEY`. That is the right
setup for one person on their own machine.

When several people share one console (a lab, a group, a course), turn on
accounts. Everyone then signs in, each project belongs to someone and is
private until they share it, and each person's model calls are charged to their
own API key.

## Turning accounts on

On the server, create the first admin:

```bash
aurora users add you@lab.org --name "Your Name" --admin
```

From then on the console asks everyone to sign in. Projects that already exist
become the first admin's projects. Nothing else changes for them.

Invite everyone else from the console (**Team → People → Invite**) or add them
on the server:

```bash
aurora users add sara@lab.org --name "Sara Novak"          # a member
aurora users list
aurora users role sara@lab.org admin                       # admin | member | guest
aurora users passwd sara@lab.org                           # forgotten password
aurora users remove sara@lab.org                           # what they made stays
```

An invitation is a one-time link, valid for 14 days. Whoever opens it chooses a
name and a password (10 characters or more).

## Who is who in the team

| Team role | Can |
|---|---|
| Admin | Manage people, groups, rules and the team key; see the activity log. Gets **no** rights in anyone's project by being admin. |
| Member | Create projects; see projects shared with them or with the whole team. |
| Guest | See only the projects they were invited to. Cannot start runs unless the team's rules allow it. |

People whose email domain is in **Team → Rules → Email domains of members** join
as members; everyone else invited to a project joins as a guest. Without any
domains set, people with the inviter's domain join as members.

Groups (**Team → Groups**) let you add, say, "Catalysis subgroup" to a project in
one step.

## Sharing a project

Open the project and press **Share** (top right), or **Share** in the project's
menu in the sidebar.

| Role | View | Comment | Edit | Start runs | Answer decisions | Share | Manage |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Owner | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Editor | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| Commenter | ✓ | ✓ | | | | | |
| Viewer | ✓ | | | | | | |

**Details** next to a person shows the seven rights one by one; changing any of
them makes the role *Custom*. Nobody can give rights they do not have, and the
last owner of a project cannot be removed.

The rest of the Share window:

* **Everyone else in the team**: no access, can see, or can see and comment.
* **Who answers a run's decisions**: whoever started the run (owners can step
  in), only the owners, or one named person. Every decision records who made it.
* **Confidential project**: hidden from the rest of the team whatever the
  setting above; skills learned in it stay in it.

Someone without view rights gets "not found" for the project, so a private
project does not even reveal that it exists. Search and Analytics only show
what you may see.

## What stays private

Each person has their own conversation with a project's front desk, and their
own notification channels. Members see a project's runs, results, files and
reports; they do not see anyone else's conversation. Owners and admins cannot
open them either. The activity log records *who did what* (signed in, shared a
project, changed a rule), never what anyone wrote.

In a run's own chat, which everyone in the project shares, each message shows
who wrote it.

## API keys

Each person adds their own OpenRouter key under the account button (bottom
left) → **Your API keys**. The key is stored encrypted on the server
(`AURORA_HOME/secret.key` holds the encryption key; keep that file private and
back it up with the database) and is never shown back, not even to admins:
they only see whether someone has one.

The key a call uses:

1. the person's own key;
2. otherwise the team key (**Team → Keys**, or the server's
   `OPENROUTER_API_KEY` when no team key is set), if **Members without a key of
   their own may use the team's key** is on and the person is not a guest;
3. otherwise none: starting a run or talking to the front desk says to add a key.

A run is charged to the person who started it, also when someone else resumes
it, and it notifies that person's channels.

## Team rules

| Rule | Default |
|---|---|
| Guests can start runs (charged to their own key) | off |
| New projects start as confidential | off |
| Members without a key of their own may use the team's key | on |

## Exposing the console

With accounts on, the console can listen on a network interface
(`aurora serve --host 0.0.0.0`) behind HTTPS (a reverse proxy such as Caddy or
nginx). Sessions last 30 days in an HttpOnly cookie. Without accounts, keep the
default `127.0.0.1` and use an SSH tunnel.
