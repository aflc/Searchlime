Searchlime
==========

Seachlime is a full text search plugin for SublimeText3 powered by [Whoosh](https://bitbucket.org/mchaput/whoosh/wiki/Home).


Settings
========

Place for indexes
-----------------

Default directory to save indexes is `~/.Searchlime`.
If you want to change this, set "indexdir" for the `Package - User` settings file.


enable/disable
--------------

Searchlime have indexes for each projects, and disabled by default.
If you want to enable with a project, Edit project settings file (`Project > Edit Project`), and like this:

```
{
    ...
    ...
    "Searchlime":
    {
        "enable": true
    }
}
```

If you want to enable for all projects, set `"enable": true` for the `Package - User` settings file.


How to use
----------

* Command `Searchlime search` > input your search word to an input panel > browse with quick panel
* `ctrl+alt+s` for Windows/Linux, or `ctrl+super+s` for OSX is a default keybind of searching.
