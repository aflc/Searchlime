import sublime
import sublime_plugin
import os
import threading
import sys
import time
from .lime import DirectoryTree

wsh = None

class Const:
    now_indexing = False
    opts = None
    paths = {}
    ix = None
    cache_ix = None


def plugin_loaded():
    global wsh
    import importlib
    whoosh_libdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'whoosh_2_5_4')
    wsh_loader = importlib.find_loader('whoosh', [whoosh_libdir])
    wsh = wsh_loader.load_module('whoosh')
    import whoosh.index
    import whoosh.fields
    import whoosh.qparser
    import whoosh.query
    global SCHEMA
    SCHEMA = wsh.fields.Schema(path=wsh.fields.ID(stored=True), mtime=wsh.fields.STORED, fsize=wsh.fields.STORED,
                               data=wsh.fields.NGRAM(stored=False, phrase=True, minsize=2, maxsize=2))
    Const.cache_ix = open_ix(load_index_dir(), '__Searchlime_cache__', create=True)


def care_path(path):
    if sublime.platform() == 'windows':
        if path[0] == '/' and len(path) > 1:
            if path[1].isalnum():
                if len(path) == 2 or path[2] == '/':
                    path = '{}:/{}'.format(path[1], path[3:])
    return os.path.normpath(path)


def is_enabled(window):
    # local switch
    project_data = window.project_data()
    if 'Searchlime' in project_data:
        if 'enable' in project_data['Searchlime']:
            return bool(project_data['Searchlime']['enable'])
    # global switch
    settings = sublime.load_settings('Searchlime.sublime-settings')
    if settings.get("enable", False):
        return True
    return False


def get_project_name(window):
    project_path = window.project_file_name()
    if not project_path:
        return
    return os.path.basename(project_path)

def get_project_dir(window):
    project_path = window.project_file_name()
    if not project_path:
        return
    return os.path.dirname(project_path)


def load_index_dir():
    settings = sublime.load_settings('Searchlime.sublime-settings')
    index_dir = settings.get('indexdir')
    if not index_dir:
        return
    index_dir = os.path.expanduser(index_dir)
    if not os.path.exists(index_dir):
        os.makedirs(index_dir)
    return index_dir


def load_options(window):
    # loading
    options = {}
    options['indexdir'] = load_index_dir()
    if not options['indexdir']:
        print('Searchlime: error: option "indexdir" not found')
        return
    settings = sublime.load_settings('Searchlime.sublime-settings')
    global_settings = sublime.load_settings('Preferences.sublime-settings')
    options['binary'] = global_settings.get('binary_file_patterns', [])
    options['exclude_files'] = global_settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] = global_settings.get('folder_exclude_patterns', [])
    # update options with Searchlime settings
    options['binary'] += settings.get('binary_file_patterns', [])
    options['exclude_files'] += settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] += settings.get('folder_exclude_patterns', [])
    # update options with project settings
    project_settings = window.project_data().get('Searchlime', {})
    options['binary'] += project_settings.get('binary_file_patterns', [])
    options['exclude_files'] += project_settings.get('file_exclude_patterns', [])
    options['exclude_dirs'] += project_settings.get('folder_exclude_patterns', [])
    # merge duplicated patterns
    options['binary'] = set(options['binary'])
    options['exclude_files'] = set(options['exclude_files'])
    options['exclude_dirs'] = set(options['exclude_dirs'])
    projname = get_project_name(window)
    if not projname:
        return
    project_dir = get_project_dir(window)
    options['project_name'] = projname
    # dirs in project
    options['folders'] = []
    for d in window.project_data().get('folders', []):
        d['path'] = care_path(os.path.join(project_dir, d['path']))
        d.setdefault('follow_symlinks', False)
        d.setdefault('file_exclude_patterns', set())
        d['file_exclude_patterns'].update(options['binary'])
        d.setdefault('folder_exclude_patterns', set()).update(options['exclude_dirs'])
        options['folders'].append(d)
    return options


def readfile(path):
    try:
        return open(path, encoding='utf-8').read()
    except UnicodeDecodeError:
        return ''


def readdata(view):
    return view.substr(sublime.Region(0, view.size()))


def update_index(paths, callback=None, remove=True):
    ix = Const.ix
    with ix.searcher() as searcher:
        with ix.writer(limitmb=256) as writer:
            # remove non-existing paths
            if remove:
                remove_paths = []
                for path in searcher.field_terms('path'):
                    if path not in paths:
                        remove_paths.append(path)
                for path in remove_paths:
                    writer.delete_by_term('path', path)
            # update existing paths
            for path in paths:
                fstat = os.stat(path)
                mtime, fsize = fstat.st_mtime_ns, fstat.st_size
                doc = searcher.document(path=path)
                if not doc or doc['mtime'] != mtime or fsize != doc['fsize']:
                    writer.delete_by_term('path', path)
                    data = readfile(path)
                    if data:
                        writer.add_document(path=path, data=data, mtime=mtime, fsize=fsize)
                if callback:
                    callback()


def update_index_with_view(view):
    if not Const.opts:
        print('Searchlime: error: cannot find options')
        return
    dt = Const.paths.get(Const.opts['project_name'])
    if dt is None:
        print('Searchlime: error: cannot find files')
        return
    path = view.file_name()
    if path not in dt.cached_items():
        return
    update_index([path], remove=False)


def open_ix(indexdir, name, create=False, recreate=False):
    if not name:
        return None
    if recreate:
        return wsh.index.create_in(indexdir, SCHEMA, indexname=name)
    elif wsh.index.exists_in(indexdir, indexname=name):
        return wsh.index.open_dir(indexdir, indexname=name)
    elif create:
        return wsh.index.create_in(indexdir, SCHEMA, indexname=name)


def create_directory_tree():
    opts = Const.opts
    if not opts:
        print('Searchlime: error: cannot find options')
    return DirectoryTree(opts['folders'])


def walk(directory, exclude_dirs=set(), exclude_files=set(), followlinks=False):
    drs = [directory]
    visited = set(drs)
    while drs:
        newdrs = []
        for dr in drs:
            for entry in os.listdir(dr):
                path = care_path(os.path.join(dr, entry))
                st = os.stat(path)
                if stat.S_ISLNK(st.st_mode) and not followlinks:
                    continue
                if stat.S_ISREG(st.st_mode):
                    if not match_pattern(path, exclude_files):
                        yield path
                elif stat.S_ISDIR(st.st_mode):
                    if path in visited:
                        continue
                    visited.add(path)
                    if not match_pattern(path, exclude_dirs):
                        newdrs.append(path)
        drs = newdrs


def match_pattern(s, patterns):
    for pat in patterns:
        if fnmatch.fnmatch(s, pat):
            return True
        if s.endswith(pat):
            return True
    return False


class SearchlimeUpdateIndexCommand(sublime_plugin.WindowCommand):
    def __init__(self, window):
        super().__init__(window)

    def run(self):
        if is_enabled(self.window) and not Const.now_indexing:
            Const.now_indexing = True
            tr = threading.Thread(target=self.run_indexing)
            tr.start()
        else:
            self.window.active_view().set_status("Searchlime", "Searchlime is disabled")

    def run_indexing(self):
        Const.opts = load_options(self.window)
        if Const.opts is None:
            print('Searchlime: cannnot load options')
            return
        self.total_files = 0
        self.num_files = 0
        projname = Const.opts['project_name']
        if projname not in Const.paths:
            Const.paths[projname] = create_directory_tree()
        dt = Const.paths[projname]
        Const.ix = open_ix(Const.opts['indexdir'], projname, create=True)
        if not Const.ix:
            self.window.active_view().set_status("Searchlime", "indexdir open error")
        paths = dt.items()
        self.total_files = len(paths)
        self.update_status()
        update_index(paths, callback=self.increment_index_count)
        Const.now_indexing = False
        self.window.active_view().set_status("Searchlime", "update index finished.")


    def increment_index_count(self):
        self.num_files += 1

    def update_status(self):
        if Const.now_indexing:
            percent = 100.0
            if self.total_files > 0:
                percent = self.num_files / self.total_files * 100
            self.window.active_view().set_status(
                "Searchlime",
                "Searchlime indexing {}/{} files({} %)".format(self.num_files, self.total_files, int(percent)))
            sublime.set_timeout(self.update_status, 2000)


class SearchlimeReindexCommand(SearchlimeUpdateIndexCommand):
    def __init__(self, window):
        super().__init__(window)

    def run_indexing(self):
        Const.opts = load_options(self.window)
        if Const.opts is None:
            print('Searchlime: cannnot load options')
            return
        self.total_files = 0
        self.num_files = 0
        projname = Const.opts['project_name']
        if projname not in Const.paths:
            Const.paths[projname] = create_directory_tree()
        dt = Const.paths[projname]
        # reindex whole project
        ix = open_ix(opts['indexdir'], projectname, recreate=True)
        if not ix:
            self.window.active_view().set_status("Searchlime", "indexdir open error")
        Const.ix = ix
        paths = dt.items()
        self.total_files = len(paths)
        self.update_status()
        update_index(paths, callback=self.increment_index_count)
        Const.now_indexing = False
        self.window.active_view().erase_status("Searchlime")


class SearchlimeEventListener(sublime_plugin.EventListener):

    def on_query_context(self, view, key, operator, operand, match_all):
        if key in ('searchlime_next_result', 'searchlime_previous_result'):
            if SearchlimeSearchCommand.instance:
                return True
        return False


class SearchlimeNextResultCommand(sublime_plugin.WindowCommand):

    def run(self):
        ins = SearchlimeSearchCommand.instance
        if ins and ins.found_regions:
            if ins.region_index < len(ins.found_regions) - 1:
                ins.region_index += 1
                move_cursor_to_target(ins.active_view, ins.found_regions[ins.region_index])


class SearchlimePreviousResultCommand(sublime_plugin.WindowCommand):

    def run(self):
        ins = SearchlimeSearchCommand.instance
        if ins and ins.found_regions:
            if ins.region_index > 0:
                ins.region_index -= 1
                move_cursor_to_target(ins.active_view, ins.found_regions[ins.region_index])


class SearchlimeSearchCommand(sublime_plugin.WindowCommand):

    instance = None

    def __init__(self, window):
        sublime_plugin.WindowCommand.__init__(self, window)
        self.searching = False
        self.search_for = ""
        self.active_view = None
        self.found_regions = None
        self.region_index = None
        self.item_index = None

    def run(self):
        view = self.window.active_view()
        selection_text = view.substr(view.sel()[0])
        if is_enabled(self.window):
            self.window.show_input_panel("Searchlime:", selection_text or self.search_for, self.search, None, None)
            return
        sublime.error_message("Searchlime disabled")

    def search(self, search_for):
        self.searching = True
        self.search_for = search_for
        tr = threading.Thread(target=self.run_search)
        tr.start()

    def run_search(self):
        ix = Const.ix
        if ix is None:
            sublime.error_message("Searchlime indexdir not founed")
            return
        opts = Const.opts
        if opts is None:
            print('Searchlime: error: cannot find options')
        self.items = []
        parser = wsh.qparser.QueryParser('data', ix.schema)
        with ix.searcher() as searcher:
            if len(self.search_for) == 1:
                query = wsh.query.Prefix('data', self.search_for)
            else:
                query = parser.parse('"{}"'.format(self.search_for))
            for hit in searcher.search(query):
                self.items.append(hit.get('path'))
                if len(self.items) > 10000:
                    break
        self.current_view = self.window.active_view()
        if self.items:
            self.__class__.instance = self
        self.show_quick_panel()

    def show_quick_panel(self, start=0):
        if self.items:
            self.item_index = start
            self.window.show_quick_panel(self.items, self.on_done, 0, start, self.on_highlighted)
        else:
            self.window.show_quick_panel(["No results"], self.on_done_none)

    def on_done(self, index):
        if index == -1:
            self.window.focus_view(self.current_view)
            flush_key(self.current_view)
            if self.active_view:
                flush_key(self.active_view)
        else:
            path = self.items[index]
            if not self.active_view:
                self.active_view = self.window.open_file(path)
            move_to_view_thread = threading.Thread(target=self.move_to_view)
            move_to_view_thread.start()
        self.__class__.instance = None

    def move_to_view(self):
        view = self.active_view
        window = view.window()
        # view = window.open_file(view.file_name())
        while view.is_loading():
            time.sleep(0.05)
        rg = self.found_regions[self.region_index]
        sel = view.sel()
        sel.clear()
        sel.add(rg)
        flush_key(view)
        view.show_at_center(rg)

    def show_view(self, view):
        while view.is_loading():
            time.sleep(0.05)
        if self.active_view != view:
            self.found_regions = view.find_all(self.search_for, sublime.IGNORECASE)
            self.region_index = 0
            if self.active_view:
                flush_key(self.active_view)
        self.active_view = view
        highlight_regions(view, self.found_regions)
        if self.found_regions:
            move_cursor_to_target(view, self.found_regions[self.region_index])

    def on_done_none(self, index):
        flush_key(self.current_view)

    def on_highlighted(self, index):
        self.item_index = index
        if index != -1:
            path = self.items[index]
            view = self.window.open_file(path, sublime.TRANSIENT)
            show_view_thread = threading.Thread(target=self.show_view, args=(view,))
            show_view_thread.start()
            view.set_status("Searchlime", "search: {} found: {} files ".format(self.search_for, len(self.items)))
        else:
            self.window.focus_view(self.current_view)
            flush_key(self.current_view)


def move_cursor_to_target(view, csr):
    topl = view.rowcol(view.visible_region().begin())[0]
    bottoml = view.rowcol(view.visible_region().end())[0]
    height = bottoml - topl
    view.add_regions('Searchlime_line', [view.line(csr)], 'comment', '', sublime.DRAW_NO_FILL)
    view.add_regions('Searchlime_csr', [csr], 'string', '', sublime.DRAW_NO_FILL)
    topl = view.rowcol(view.visible_region().begin())[0]
    view.show_at_center(view.text_point(view.rowcol(csr.a)[0] - height / 4, 0))


def highlight_regions(view, regions):
    if len(regions) > 0:
        view.add_regions("Searchlime_regions", regions,
                         "entity.name.filename.find-in-files", "dot", sublime.DRAW_OUTLINED)
        view.set_status("Searchlime_regions", "regions: " + str(len(regions)))


def flush_key(view):
    view.erase_status("Searchlime")
    view.erase_status("Searchlime_regions")
    view.erase_regions("Searchlime_line")
    view.erase_regions("Searchlime_csr")
    view.erase_regions("Searchlime_regions")


class SearchlimeUpdateEvent(sublime_plugin.EventListener):
    def on_activated_async(self, view):
        window = view.window()
        if self.will_be_call(window):
            window.run_command('searchlime_update_index')


    def on_post_save_async(self, view):
        window = view.window()
        if self.can_update_view_of_index(window):
            Const.now_indexing = True
            update_index_with_view(view)
            Const.now_indexing = False


    def can_update_view_of_index(self, window):
        if window and Const.opts and Const.ix and not Const.now_indexing:
            projname = get_project_name(window)
            if not projname:
                return False
            if projname == Const.opts['project_name']:
                return True
        return False

    def will_be_call(self, window):
        if not window:
            return False
        projname = get_project_name(window)
        if not projname:
            return False
        if not Const.opts or projname != Const.opts['project_name']:
            Const.opts = load_options(window)
            if not Const.opts:
                return False
            return True
