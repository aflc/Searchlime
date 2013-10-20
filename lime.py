import os
import stat
import fnmatch


class DirectoryTree:

    '''DirectoryTreeを扱うクラス。
    あるroot directory以下の走査、パスのタイプチェック(file, directory, symbolic link, ...)を行う。
    Tree情報はキャッシュされ、次回の走査時に効率的に動作する。
    '''

    def __init__(self, info):
        '''infoには辞書オブジェクトのリストを格納する。
        これはSublimeText標準のフォーマットに準拠する。すなわち、
        info = [
          {'path': '/path/to/root', 'follow_symlinks': True,
           'folder_exclude_patterns': set([...]), 'file_exclude_patterns': set([...])},
        ]
        といったものである。全て存在する必要がある。
        '''
        self.info = info
        self.tree_cache = {}
        self.item_cache = []

    def set_info(self, info):
        self.info = info

    def cached_items(self):
        return self.item_cache

    def items(self):
        ''' treeをtop downで走査して返す。返り値はファイルリスト。
        ただしキャッシュが古く、同名のディレクトリである可能性がある。
        '''
        items = []
        visited = set()
        for info in self.info:
            drs = [info['path']]
            visited.add(info['path'])
            while drs:
                newdrs = []
                for dr in drs:
                    cache = self.tree_cache.get(dr, {})
                    # directoryが本当にdirectoryかチェックする
                    try:
                        entries = os.listdir(dr)
                        paths = [os.path.join(dr, x) for x in entries]
                    except NotADirectoryError:
                        # rare case. remove cache bwloe this directory, and add file this time
                        removekeys = []
                        for k in self.tree_cache.keys():
                            if k.startswith(dr):
                                removekeys.append(k)
                        for k in removekeys:
                            del self.tree_cache[k]
                        tp = check_type(path)
                        if tp:
                            if not info['follow_symlinks'] and tp[1]:
                                continue  # symlinkを辿らない
                            elif tp[0] == 'file':
                                if not match_pattern(path, info['file_exclude_patterns']):
                                    items.append(path)
                            continue
                    for path in paths:
                        # それぞれのエントリーをキャッシュから検索
                        tp = cache.setdefault(path, check_type(path))
                        if not tp:
                            continue
                        if not info['follow_symlinks'] and tp[1]:
                            continue  # symlinkを辿らない
                        if tp[0] == 'file':
                            if not match_pattern(path, info['file_exclude_patterns']):
                                items.append(path)
                        elif tp[0] == 'dir':
                            if not match_pattern(path, info['folder_exclude_patterns']):
                                newdrs.append(path)
                drs = newdrs
        self.item_cache = items
        return items


def match_pattern(s, patterns):
    for pat in patterns:
        if fnmatch.fnmatch(s, pat):
            return True
        if s.endswith(pat):
            return True
    return False


def check_type(path):
    st = os.stat(path)
    islink = stat.S_ISLNK(st.st_mode)
    if stat.S_ISREG(st.st_mode):
        return ('file', islink)
    elif stat.S_ISDIR(st.st_mode):
        return ('dir', islink)
