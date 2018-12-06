from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random
import numpy as np
import pickle
import time

import torch
import torch.nn as nn
from torch import optim
import torch.nn.functional as F
import pdb

import matplotlib.pyplot as plt
plt.switch_backend('agg')

from model_architectures import Encoder_RNN, Decoder_RNN
from data_prep import prepareData, tensorsFromPair, prepareNonTrainDataForLanguagePair, load_cpickle_gc
from inference import generate_translation
from misc import timeSince, load_cpickle_gc

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


BATCH_SIZE = 32
PAD_token = 0
SOS_token = 1
EOS_token = 2
UNK_token = 3
teacher_forcing_ratio = 1.0
MAX_LENGTH = 100

import numpy as np
import torch
from torch.utils.data import Dataset

class LanguagePairDataset(Dataset):
    
    def __init__(self, sent_pairs): 
        # this is a list of sentences 
        self.sent_pairs_list = sent_pairs

    def __len__(self):
        return len(self.sent_pairs_list)
        
    def __getitem__(self, key):
        """
        Triggered when you call dataset[i]
        """
        sent1 = self.sent_pairs_list[key][0][:MAX_LENGTH]
        sent2 = self.sent_pairs_list[key][1][:MAX_LENGTH]
        return [sent1, sent2, len(sent1), len(sent2)]

def language_pair_dataset_collate_function(batch):
    """
    Customized function for DataLoader that dynamically pads the batch so that all 
    data have the same length
    """
    sent1_list = []
    sent1_length_list = []
    sent2_list = []
    sent2_length_list = []
    # padding
    for datum in batch:
        padded_vec_1 = np.pad(np.array(datum[0]).T.squeeze(), pad_width=((0,MAX_LENGTH-len(datum[0]))), 
                                mode="constant", constant_values=PAD_token)
        padded_vec_2 = np.pad(np.array(datum[1]).T.squeeze(), pad_width=((0,MAX_LENGTH-len(datum[1]))), 
                                mode="constant", constant_values=PAD_token)
        sent1_list.append(padded_vec_1)
        sent2_list.append(padded_vec_2)
        sent1_length_list.append(len(datum[0]))
        sent2_length_list.append(len(datum[1]))
    return [torch.from_numpy(np.array(sent1_list)), torch.cuda.LongTensor(sent1_length_list), 
            torch.from_numpy(np.array(sent2_list)), torch.cuda.LongTensor(sent2_length_list)]
#train_idx_pairs = load_cpickle_gc("train_vi_en_idx_pairs")

class Encoder_Batch_RNN(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(Encoder_Batch_RNN, self).__init__()
        self.hidden_size = hidden_size
        self.embedding = nn.Embedding(input_size, hidden_size)
        self.gru = nn.GRU(hidden_size, hidden_size, batch_first=True)
        
    def init_hidden(self, batch_size):
        return torch.zeros(1, batch_size, self.hidden_size, device=device)

    def forward(self, sents, sent_lengths):
        '''
            sents is a tensor with the shape (batch_size, padded_length )
            when we evaluate sentence by sentence, you evaluate it with batch_size = 1, padded_length.
            [[1, 2, 3, 4]] etc. 
        '''
        batch_size = sents.size()[0]
        sent_lengths = list(sent_lengths)
        # We sort and then do pad packed sequence here. 
        descending_lengths = [x for x, _ in sorted(zip(sent_lengths, range(len(sent_lengths))), reverse=True)]
        descending_indices = [x for _, x in sorted(zip(sent_lengths, range(len(sent_lengths))), reverse=True)]
        descending_lengths = np.array(descending_lengths)
        descending_sents = torch.index_select(sents, 0, torch.tensor(descending_indices).to(device))
        
        # get embedding
        embed = self.embedding(descending_sents)
        # pack padded sequence
        embed = torch.nn.utils.rnn.pack_padded_sequence(embed, descending_lengths, batch_first=True)
        
        # fprop though RNN
        self.hidden = self.init_hidden(batch_size)
        rnn_out, self.hidden = self.gru(embed, self.hidden)
        
        # change the order back
        change_it_back = [x for _, x in sorted(zip(descending_indices, range(len(descending_indices))))]
        self.hidden = torch.index_select(self.hidden, 1, torch.LongTensor(change_it_back).to(device)) 
        
        # **TODO**: What is rnn_out?
        return rnn_out, self.hidden

class Decoder_Batch_RNN(nn.Module):
    def __init__(self, output_size, hidden_size):
        super(Decoder_Batch_RNN, self).__init__()
        self.hidden_size = hidden_size

        self.embedding = nn.Embedding(output_size, hidden_size)
        self.gru = nn.GRU(hidden_size, hidden_size, batch_first=True)
        self.out = nn.Linear(hidden_size, output_size)
        self.softmax = nn.LogSoftmax(dim=1)
        
    def init_hidden(self, batch_size):
        return torch.zeros(1, batch_size, self.hidden_size, device=device)

    def forward(self, sents, sent_lengths, hidden):
        '''
        For evaluate, you compute [batch_size x ] [[1]]
        '''
        batch_size = sents.size()[0]
        sent_lengths = list(sent_lengths)
        
        descending_lengths = [x for x, _ in sorted(zip(sent_lengths, range(len(sent_lengths))), reverse=True)]
        descending_indices = [x for _, x in sorted(zip(sent_lengths, range(len(sent_lengths))), reverse=True)]
        descending_lengths = np.array(descending_lengths)
        descending_sents = torch.index_select(sents, 0, torch.tensor(descending_indices).to(device))
        
        # get embedding
        embed = self.embedding(descending_sents)
        # pack padded sequence
        embed = torch.nn.utils.rnn.pack_padded_sequence(embed, descending_lengths, batch_first=True)
        
        # fprop though RNN
        self.hidden = hidden
        rnn_out, self.hidden = self.gru(embed, self.hidden)
        
        change_it_back = [x for _, x in sorted(zip(descending_indices, range(len(descending_indices))))]
        self.hidden = torch.index_select(self.hidden, 1, torch.LongTensor(change_it_back).to(device))
        rnn_out, _ = torch.nn.utils.rnn.pad_packed_sequence(rnn_out, batch_first=True)
        # rnn_out is batch_size x 28 x 256
        """      
        final_hidden = self.hidden
        final_hidden = final_hidden.view(final_hidden.size(1), final_hidden.size(0), -1)
        first_hidden = hidden
        first_hidden = first_hidden.view(first_hidden.size(1), first_hidden.size(0), -1)
        
        rnn_out = torch.cat((first_hidden, rnn_out, final_hidden), 1)
        
        """
#         rnn_out = rnn_out.view(-1, rnn_out.size(2))
        output = self.softmax(self.out(rnn_out))
        # now output is the size 28 by 31257 (vocab size)
        return output, hidden

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

def greedy_search(decoder, decoder_input, hidden, max_length):
    translation = []
    for i in range(max_length):
        next_word_softmax, hidden = decoder(decoder_input, hidden)
        best_idx = torch.max(next_word_softmax, 1)[1].squeeze().item()

        # convert idx to word
        best_word = target_lang.index2word[best_idx]
        translation.append(best_word)
        decoder_input = torch.tensor([[best_idx]], device=device)
        
        if best_word == 'EOS':
            break
    return translation



def beam_search(decoder, decoder_input, hidden, max_length, k):
    
    candidates = [(decoder_input, 0, hidden)]
    potential_candidates = []
    completed_translations = []

    # put a cap on the length of generated sentences
    for m in range(max_length):
        for c in candidates:
            # unpack the tuple
            c_sequence = c[0]
            c_score = c[1]
            c_hidden = c[2]
            # EOS token
            if c_sequence[-1] == 1:
                completed_translations.append((c_sequence, c_score))
                k = k - 1
            else:
                next_word_probs, hidden = decoder(c_sequence[-1], c_hidden)
                # in the worst-case, one sequence will have the highest k probabilities
                # so to save computation, only grab the k highest_probability from each candidate sequence
                top_probs, top_idx = torch.topk(next_word_probs, k)
                for i in range(len(top_probs[0])):
                    word = torch.from_numpy(np.array([top_idx[0][i]]).reshape(1, 1)).to(device)
                    new_score = c_score + top_probs[0][i]
                    potential_candidates.append((torch.cat((c_sequence, word)).to(device), new_score, hidden))

        candidates = sorted(potential_candidates, key= lambda x: x[1], reverse=True)[0:k] 
        potential_candidates = []

    completed = completed_translations + candidates
    completed = sorted(completed, key= lambda x: x[1], reverse=True)[0] 
    final_translation = []
    for x in completed[0]:
        final_translation.append(target_lang.index2word[x.squeeze().item()])
    return final_translation

def generate_translation(encoder, decoder, sentence, max_length, search="greedy", k = None):
    """ 
    @param max_length: the max # of words that the decoder can return
    @returns decoded_words: a list of words in target language
    """    
    with torch.no_grad():
        input_tensor = sentence
        input_length = sentence.size()[0]
        
        # encode the source sentence
        encoder_hidden = encoder.init_hidden(1)
        encoder_output, encoder_hidden = encoder(input_tensor.view(1, -1),torch.tensor([input_length]))
        # start decoding
        decoder_input = torch.tensor([[SOS_token]], device=device)  # SOS
        decoder_hidden = encoder_hidden
        decoded_words = []
        
        if search == 'greedy':
            decoded_words = greedy_search(decoder, decoder_input, decoder_hidden, max_length)
        elif search == 'beam':
            if k is None:
                k = 2 # since k = 2 preforms badly
            decoded_words = beam_search(decoder, decoder_input, decoder_hidden, max_length, k)  

        return decoded_words

def evaluate(encoder, decoder, sentence, search="greedy", max_length=MAX_LENGTH):
    """
    Function that generate translation.
    First, feed the source sentence into the encoder and obtain the hidden states from encoder.
    Secondly, feed the hidden states into the decoder and unfold the outputs from the decoder.
    Lastly, for each outputs from the decoder, collect the corresponding words in the target language's vocabulary.
    And collect the attention for each output words.
    @param encoder: the encoder network
    @param decoder: the decoder network
    @param sentence: string, a sentence in source language to be translated
    @param max_length: the max # of words that the decoder can return
    @output decoded_words: a list of words in target language
    @output decoder_attentions: a list of vector, each of which sums up to 1.0
    """    
    # process input sentence
    with torch.no_grad():
        input_tensor = sentence
        input_length = input_tensor.size()[0]
        # encode the source lanugage
        encoder_hidden = encoder.init_hidden(1)

        encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)

        for ei in range(input_length):
            encoder_output, encoder_hidden = encoder(input_tensor[ei],
                                                     encoder_hidden)
            encoder_outputs[ei] += encoder_output[0, 0]
        decoder_input = torch.tensor([[SOS_token]], device=device)  # SOS
        # decode the context vector
        decoder_hidden = encoder_hidden # decoder starts from the last encoding sentence
        # output of this function
        decoder_attentions = torch.zeros(max_length, max_length)
        
        if search == 'greedy':
            decoded_words = greedy_search(decoder, decoder_input, decoder_hidden, max_length)
        elif search == 'beam':
            decoded_words = beam_search(decoder, decoder_input, decoder_hidden, max_length)  
        return decoded_words

import sacrebleu
def calculate_bleu(predictions, labels):
    """
    Only pass a list of strings 
    """
    # tthis is ony with n_gram = 4

    bleu = sacrebleu.raw_corpus_bleu(predictions, [labels], .01).score
    return bleu



MAX_LENGTH = 10
def test_model(encoder, decoder, search, test_pairs, lang1, max_length=MAX_LENGTH):
    # for test, you only need the lang1 words to be tokenized,
    # lang2 words is the true labels
    encoder_inputs = [pair[0] for pair in test_pairs]
    true_labels = [pair[1] for pair in test_pairs]
    translated_predictions = []
    for i in range(len(encoder_inputs)):
        if i% 100== 0:
            print(i)
        e_input = encoder_inputs[i]
        decoded_words = generate_translation(encoder, decoder, e_input, search=search, max_length=MAX_LENGTH)
        translated_predictions.append(" ".join(decoded_words))
    start = time.time()
    print("bleurg")
    bleurg = calculate_bleu(translated_predictions, true_labels)
    print(time.time() - start)
    return bleurg

def save_model(encoder, decoder, val_accs, train_accs, title):
    val_accs = np.array(val_accs) # this is the BLEU score. 
    max_val = val_accs.max() 
    train_accs = np.array(train_accs)
    link = title.replace(" ", "")
    torch.save(encoder.state_dict(), "output/"+link + "encodermodel_states")
    torch.save(decoder.state_dict(), "output/"+link + "decodermodel_states")
    pickle.dump(val_accs, open("output/"+link + "val_accuracies", "wb"))
    pickle.dump(train_accs, open("output/"+link + "train_accuracies", "wb"))
    pickle.dump(max_val, open("output/"+link + "maxvalaccis"+str(max_val), "wb"))
    # this is when you want to overlay
    num_in_epoch = np.shape(train_accs)[1]
    num_epochs = np.shape(train_accs)[0]
    x_vals = np.arange(0, num_epochs, 1.0/float(num_in_epoch))
    fig = plt.figure()
    plt.title(title)
    # plot the title of this data. 
    plt.plot(x_vals, train_accs.flatten(), label="Training Accuracy (NLLoss")
    plt.plot(x_vals, val_accs.flatten(), label="Validation Accuracy (BLEU score)")
    plt.legend(loc="lower right")
    plt.ylabel("Accuracy of Model")
    plt.xlabel("Epochs (Batch Size 32)")
    plt.ylim(0,100)
    plt.xlim(0, num_epochs)
    plt.yticks([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    plt.xticks(np.arange(num_epochs + 1))
    fig.savefig("output/"+link+"graph.png")

    
def trainIters(encoder, decoder, n_epochs, pairs, validation_pairs, lang1, lang2, search, title, max_length_generation,  print_every=1000, plot_every=1000, learning_rate=0.0001):
    """
    lang1 is the Lang object for language 1 
    Lang2 is the Lang object for language 2
    Max length generation is the max length generation you want 
    """
    start = time.time()
    plot_losses = []
    val_losses = [] 
    print_loss_total = 0  # Reset every print_every
    plot_loss_total = 0  # Reset every plot_every
    val_loss_total = 0
    plot_val_loss = 0
    encoder_optimizer = torch.optim.Adam(encoder.parameters(), lr=learning_rate)
    decoder_optimizer = torch.optim.Adam(decoder.parameters(), lr=learning_rate)
    
    criterion = nn.NLLLoss(ignore_index=PAD_token) # this ignores the padded token. 
    plot_loss =[]
    val_loss = []
    for epoch in range(n_epochs):
        plot_loss = []
        val_loss = []
        for step, (sent1s, sent1_lengths, sent2s, sent2_lengths) in enumerate(train_loader):
            encoder.train() # what is this for?
            sent1_batch, sent2_batch = sent1s.to(device), sent2s.to(device) 
            sent1_length_batch, sent2_length_batch = sent1_lengths.to(device), sent2_lengths.to(device)
            
            encoder_optimizer.zero_grad()
            decoder_optimizer.zero_grad()
            outputs, encoder_hidden = encoder(sent1_batch, sent1_length_batch)
            # encoder outputs is currently size 696 x 256
            encoder_hidden_batch = encoder_hidden 
            decoder_hidden = encoder_hidden_batch
            
            decoder_input = torch.tensor([[SOS_token]], device=device)
            use_teacher_forcing = True
            
            loss = 0
            outputs, decoder_hidden = decoder(sent2_batch, sent2_length_batch, decoder_hidden)
            
            count = 1
            for i in range(len(sent2_batch)):

                loss = criterion(outputs[i], sent2_batch[i])
                print_loss_total += loss.item()
                plot_loss_total += loss.item()
                count += 1 # IS IT SUPPOSED TO BE 1? 
            # we also have tomaks when it's an eOS tag. 
            loss.backward()
            encoder_optimizer.step()
            decoder_optimizer.step()

            if  (step+1) % print_every == 0:
                # lets train and polot at the same time. 
                print_loss_avg = print_loss_total / (count)
                print_loss_total = 0
                print('TRAIN SCORE %s (%d %d%%) %.4f' % (timeSince(start, step / n_epochs),
                                             step, step / n_epochs * 100, print_loss_avg))
                v_loss = test_model(encoder, decoder, search, validation_pairs, lang1, max_length=MAX_LENGTH)
                # returns bleu score
                print("VALIDATION BLEU SCORE: "+str(v_loss))
                val_loss.append(v_loss)
                plot_loss_avg = plot_loss_total / plot_every
                plot_loss.append(plot_loss_avg)
                plot_loss_total = 0
        plot_losses.append(plot_loss)
        val_losses.append(val_loss)
        save_model(encoder, decoder, val_losses, plot_losses, title)
    assert len(val_losses) == len(plot_losses)
    save_model(encoder, decoder, val_losses, plot_losses, title)

hidden_size = 256
print(BATCH_SIZE)
input_lang = pickle.load(open("preprocessed_data_no_elmo/iwslt-vi-eng/preprocessed_no_elmo_vilang", "rb"))
target_lang = pickle.load(open("preprocessed_data_no_elmo/iwslt-vi-eng/preprocessed_no_elmo_englang", "rb"))
train_idx_pairs = load_cpickle_gc("preprocessed_data_no_elmo/iwslt-vi-eng/preprocessed_no_indices_pairs_train_tokenized")
val_pairs = load_cpickle_gc("preprocessed_data_no_elmo/iwslt-vi-eng/preprocessed_no_indices_pairs_validation_tokenized")
train_dataset = LanguagePairDataset(train_idx_pairs)
train_loader = torch.utils.data.DataLoader(dataset=train_dataset, 
                                           batch_size=BATCH_SIZE, 
                                           collate_fn=language_pair_dataset_collate_function,
                                          )

encoder1 = Encoder_Batch_RNN(input_lang.n_words, hidden_size).to(device)
decoder1 = Decoder_Batch_RNN(target_lang.n_words, hidden_size).to(device)

args = {
    'n_epochs': 10,
    'learning_rate': 0.001,
    'search': 'beam',
    'encoder': encoder1,
    'decoder': decoder1,
    'lang1': input_lang, 
    'lang2': target_lang,
    "pairs":train_idx_pairs, 
    "validation_pairs": val_pairs, 
    "title": "Training Curve for Basic 1-Directional Encoder Decoder Model",
    "max_length_generation": 20
}

print(BATCH_SIZE)

trainIters(**args)

