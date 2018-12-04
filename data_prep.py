from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random
import numpy as np
import torch

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

SOS_token = 0
EOS_token = 1
MAX_LENGTH = 30

class Lang:
    def __init__(self, name):
        self.name = name
        self.word2index = {}
        self.word2count = {}
        self.index2word = {0: "SOS", 1: "EOS", 2:"UNK"}
        self.n_words = 3  # Count SOS and EOS

    def addSentence(self, sentence):
        for word in sentence.split(' '):
            self.addWord(word)

    def addWord(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1

# Turn a Unicode string to plain ASCII: http://stackoverflow.com/a/518232/2809427
def unicodeToAscii(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )

# lowercase, trim, and remove non-letter characters
def normalizeString(s):
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r"([.!?])", r" \1", s)
    s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
    return s

def filterPair(p):
    return len(p[0].split(' ')) < MAX_LENGTH and \
        len(p[1].split(' ')) < MAX_LENGTH 

def filterPairs(pairs):
    return [pair for pair in pairs if filterPair(pair)]


def readLangs(input_file, target_file, input_lang, target_lang, size=None):
    print("Reading lines...")

    # Read the file and split into lines
    with open(input_file, encoding='utf-8') as file:
        if size == None:
            input_lines = open(input_file, encoding='utf-8').read().strip().split("\n")
        else:
            input_lines = [next(file).strip() for x in range(size)]
        
    with open(target_file, encoding='utf-8') as file:
        if size == None:
            target_lines = open(target_file, encoding='utf-8').read().strip().split("\n")
        else:
            target_lines = [next(file).strip() for x in range(size)]
        
    if input_lang == "zh":
        target_pairs = [normalizeString(s) for s in target_lines]
        pairs = list(zip(input_lines, target_pairs))
    else:
        lines = list(zip(input_lines, target_lines))
        # Split every line into pairs and normalize
        pairs = [[normalizeString(s) for s in l] for l in lines]
    print(pairs[0])

    input_lang = Lang(input_lang)
    target_lang = Lang(target_lang)

    return input_lang, target_lang, pairs


def processReference(lang, sentence):
    # what this does is basicallyp prepares thee refernece and removes the <UNK> data. 
    # lang1 - str 
    # lang2 - str 
    words = sentence.split(" ")
    current = []
    for word in words:
        if lang.word2index.get(word) is not None:
            current.append(word)
        else:
            current.append("UNK")
    return " ".join(current)

def prepareNonTrainDataForLanguagePair(input_file_path_dev, target_file_path_dev, input_file_path_test, target_file_path_test, input_lang, target_lang,  dirlink=""):
    # this function prepares the dataset for both balidaition adn train. 
    # input_lang and output_lang are the Lang class items  
    # dirlink is a string that takes in if you have any folders where you want to save the data. 
    # for example for Yada, dirlink would be "preprocessed_data_no_elmo/" beause thats where she stores her data. "
    # input_file_paht is the file path to the input data. 
    pairs = []
    for dataset in ["validation", "test"]:
        if dataset == "validation":
            source_language =  open(input_file_path_dev, encoding='utf-8').read().strip().split("\n")
            target_language  = open(target_file_path_dev, encoding='utf-8').read().strip().split("\n")
        else:
            source_language = open(input_file_path_test, encoding='utf-8').read().strip().split("\n")
            target_language = open(target_file_path_test, encoding='utf-8').read().strip().split("\n")
        if lang1 == "vi":
            tensors_input = [tensorFromSentence(input_lang, normalizeString(s), 0) for s in source_language]
        elif lang1 == "zh":
            # don't normalize
            tensors_input = [tensorFromSentence(input_lang,s, 0) for s in source_language]
        reference_convert =[prepareReference(target_lang, normalizeString(s)) for s in target_language]
        final_pairs = list(zip(tensors_input, reference_convert))
        pickle.dump(final_pairs, open(input_lang.name+"-"+target_lang.name+dataset+"_tokenized", "wb"))
        pairs.append(final_pairs)
    return pairs

def prepareDataInitial(lang1, lang2):
# This sts up everything you need for preprocessing. 
    input_file = 'iwslt-zh-en/train.tok.zh'
    target_file = 'iwslt-zh-en/train.tok.en'
    input_lang_train, target_lang_train, pairs = prepareTrainData(input_file, target_file, 'zh', 'eng', size=50000)
    pickle.dump(pairs, open("preprocessed_data_no_elmo/iwslt-zh-eng/preprocessed_no_indices_pairs_train", "wb"))
    pickle.dump(input_lang_train, open("preprocessed_data_no_elmo/iwslt-"+lang1+"-"+lang2+"/preprocessed_no_elmo_"+lang1+"lang", "wb"))
    pickle.dump(target_lang_train, open("preprocessed_data_no_elmo/iwslt-"+lang1+"-"+lang2+"/preprocessed_no_elmo_"+lang2+"lang", "wb"))
    lang2 = "eng"
    for lang1 in ["zh", "vi"]:
        for dataset in ["validation", "test"]:
            input_lang = load_cpickle_gc("preprocessed_data_no_elmo/iwslt-"+lang1+"-"+lang2+"/preprocessed_no_elmo_"+lang1+"lang")
            target_lang = load_cpickle_gc("preprocessed_data_no_elmo/iwslt-"+lang1+"-"+lang2+"/preprocessed_no_elmo_englang")
            if dataset == "validation":
                source_language =  open("iwslt-"+lang1+"-en/dev.tok."+lang1, encoding='utf-8').read().strip().split("\n")
                actual_english_test = open("iwslt-"+lang1+"-en/dev.tok.en", encoding='utf-8').read().strip().split("\n")
            else:
                source_language = open("iwslt-"+lang1+"-en/"+dataset+".tok."+lang1, encoding='utf-8').read().strip().split("\n")
                actual_english_test = open("iwslt-"+lang1+"-en/"+dataset+".tok.en", encoding='utf-8').read().strip().split("\n")
            if lang1 == "vi":
                tensors_input = [tensorFromSentence(input_lang, normalizeString(s), 0) for s in source_language]
            elif lang1 == "zh":
                # don't normalize
                tensors_input = [tensorFromSentence(input_lang,s, 0) for s in source_language]
            reference_convert =[prepareReference(target_lang, normalizeString(s)) for s in actual_english_test]
            final_pairs = list(zip(tensors_input, reference_convert))
            pdb.set_trace()
            pickle.dump(final_pairs, open("preprocessed_data_no_elmo/iwslt-"+lang1+"-"+lang2+"/preprocessed_no_indices_pairs_"+ dataset+"_tokenized", "wb"))

def prepareData(input_file, target_file, input_lang, target_lang, size=None):
    
    input_lang, target_lang, pairs = readLangs(input_file, target_file, input_lang, target_lang, size)
    print("Read %s sentence pairs" % len(pairs))
    pairs = filterPairs(pairs)
    print("Trimmed to %s sentence pairs" % len(pairs))
    print("Counting words...")
    print(pairs[0])
    for pair in pairs:
        input_lang.addSentence(pair[0])
        target_lang.addSentence(pair[1])
    print("Counted words:")
    print(input_lang.name, input_lang.n_words)
    print(target_lang.name, target_lang.n_words)
    return input_lang, target_lang, pairs

def indexesFromSentence(lang, sentence):
    words = sentence.split(' ')
    indices = []
    for word in words:
        if lang.word2index.get(word) is not None:
            indices.append(lang.word2index[word])
        else:
            indices.append(1) # UNK_INDEX
    return indices

def tensorFromSentence(lang, sentence):
    indexes = indexesFromSentence(lang, sentence)
    indexes.append(EOS_token)
    return torch.tensor(indexes, dtype=torch.long, device=device).view(-1, 1)


def tensorsFromPair(pair, input_lang, target_lang):
    input_tensor = tensorFromSentence(input_lang, pair[0])
    target_tensor = tensorFromSentence(target_lang, pair[1])
    return (input_tensor, target_tensor)